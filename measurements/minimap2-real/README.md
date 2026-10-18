# minimap2 on a real workload — a whole 30× HiFi genome, Graviton4 vs Intel vs AMD

> **AMD EPYC aligned the whole genome 1.48× faster than Graviton4 and was the cheapest per
> result for compute. Intel was the slowest and the dearest.** Every identity held on all three
> chips. The alignments agree record for record, apart from 9 of 11,120,030 records, where a
> MAPQ or DP score differs by one inside the chr4 centromere.

Workload: **the complete NA12878 PacBio HiFi deposit** on GIAB: 9,080,816 reads, 90.47 Gbp,
~29×. Every read in it is aligned with `-ax map-hifi --MD` against the whole GRCh38 no-alt
analysis set (195 sequences, 3.10 Gbp), using one index build. This is what a long-read lab
does once per sample, so it is not a slice. The recipe's chr20 run is 2.4% of it.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/minimap2-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # 2 prep tasks, ~25 min, ~$0.75
for a in arm64 x86_64 amd; do
  for t in genome sort truth; do
    spawn task run --spec "$(python3 scripts/spec_arch.py minimap2 $a "$COOKBOOK_BUCKET" "$(date +%s)" \
      measurements/minimap2-real/$t.task.json)" --wait
  done
done
```

`genome.sh` (pinned by sha256) builds the index once and then aligns the 25 read chunks in order
(one per chromosome, plus one for the rest). Each chunk's SAM is summarised, hashed and deleted
before the next chunk starts, so the staging tmpfs only has to hold the reads plus one chunk's SAM.
minimap2 maps each read independently, so chunking changes no alignment (shown below). The reads
minimap2 placed on chr20 go on to `sort` and `truth`. `mkspec.py` writes every spec.

## Results

32 threads on an `r8*.8xlarge` per chip (256 GiB). The recipe's own pinned minimap2 2.31-r1302
builds were used: aarch.bio on arm64 and bioconda on x86. All chips read the same input bytes:

| chip | instance | index | **align wall** | task window | $/hr | **compute $/result** | billed $/result | peak RSS |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.8xlarge` | 33 s | 2,618 s | 4,001 s | 1.8851 | 1.388 | **2.095** | 25.8 GB |
| Xeon 6975P-C | `r8i.8xlarge` | 38 s | 2,880 s | 5,052 s | 2.2227 | 1.802 | 3.119 | 26.0 GB |
| **EPYC 9R45** | `r8a.8xlarge` | 29 s | **1,774 s** | 3,566 s | 2.5562 | **1.280** | 2.532 | 26.1 GB |

Compute $/result is index plus align at the hourly price. Billed $/result is spawn's cost for the
task window: stage-in, the sha256 check, the run and its checks, and stage-out. Sorting and the
truth check on the chr20 reads add $0.015–0.018 per chip on an `m8*.2xlarge`.

- **AMD is 1.48× faster than Graviton4, and Intel is 0.91×.** The r8a's 32 vCPUs are 32 full
  cores (threads-per-core 1). The r8i's 32 vCPUs are 16 cores × 2 SMT threads (threads-per-core 2),
  and that is the likely reason for Intel's place. Per result, AMD's speed outweighs a 36% higher
  hourly price (compute 8% cheaper than Graviton4). Intel pays 18% more per hour and is also
  slower (30% dearer).
- **Billed is not compute.** Of each window, 333–431 s is staging 75 GiB in, and 74–257 s is
  checking the pins. Graviton4's `sha256sum` was 3× slower than x86's. A further 760–1,611 s is
  the in-task checks: awk over 11.1M SAM records. That last figure measures awk, not the chip:
  the x86 image runs BusyBox `awk`, which is ~2× slower than the GNU `awk` in the arm64 image.
  That is why Graviton4 wins billed while AMD wins compute. A production run without the
  verification would bill close to the compute column plus staging.
- **The rate varies 3× along the genome**, so no chromosome predicts the whole. On Graviton4,
  chr1 aligned at 17 Mbp/s (434 s for 7.58 Gbp) and chr2 at 56 Mbp/s (129 s for 7.29 Gbp). The
  whole genome averaged 35 Mbp/s. Extrapolating from chr20 (24 Mbp/s) would have predicted a
  3,770 s align, 44% more than the 2,618 s it took. The slow chunks are the ones carrying large
  satellite arrays: chr1 and the "other" chunk of unplaced contigs (198 s for 1.49 Gbp). That
  is a correlation; the run did not profile per region.
- **Staging, not memory, picks the family.** minimap2 peaked at 26 GB on every chip. The tmpfs
  peaked at **95 GiB**: 75 GiB of reads, the 3.1 GB reference, the 7 GB index and chr1's 16 GB
  SAM. Staging tmpfs is half of RAM, so a 128 GiB box (64 GiB tmpfs) cannot hold this. Hence `r8`.
- During alignment the chips kept 23.9, 23.6 and 21.6 of 32 threads busy (74%, 74%, 67%, from
  minimap2's own CPU/real accounting). The time is lost in chunk tails and single-threaded input
  parsing.
- n = 1 per cell. minimap2 is deterministic here: a rerun of the chr4 chunk on each architecture
  reproduced that chip's bytes exactly (`results/chr4-diff.txt`, `chr4-diff-*.task.json`).

## What held at scale, on all three chips

| claim | why it must hold | observed (identical on all three) |
|---|---|---|
| staged bytes == pinned sha256 | provenance | 54 files |
| primary records == input reads, per chunk and in total | conservation: each read accounted for once | 9,080,816 in 25 chunks |
| no read has two primaries; read names unique across chunks | the read set is a set | 0 duplicates |
| every read is one of the deposit's primaries | the read set is what it claims | 9,080,816 of 9,080,816 |
| mapped ≥ 95% / mean identity ≥ 98% | the recipe's floors ([why 98, not 99](../../recipes/minimap2/README.md)) | 99.716% / 98.7919% |
| where both are confident (MAPQ ≥ 30), minimap2's locus overlaps pbmm2's ≥ 0.99 | the depositors' own aligner on the same reference | **0.99897** (8,621,026 of 8,629,939) |
| reads placed on chr20: sorted BAM records == SAM records, BAM magic, EOF | lossless sort | 270,050; `1f8b0804` |
| GIAB v4.2.1 chr20 truth SNVs recovered ≥ 0.95 | published truth; misplacement hides known variants | **0.99960** (69,209 / 28) |

Two identities came free from the chunking. **The chr20 chunk FASTQ is byte-identical to the
recipe's** (`fdb979ce…`): same records, same image, same `gzip -1`. **Its SAM is byte-identical
to the probe's** (`a7fcd7e4…`), and the probe used 16 threads and an index built from FASTA where
the whole run used 32 threads and a prebuilt `.mmi`. So neither thread count nor batching changes
an alignment. Against the whole genome rather than chr20 alone, 98.09% of the chr20 reads map
(the chr20-only recipe gets 97.46%). 1.38% of the mapped reads land off chr20. Truth recall moves
from 0.99961 to 0.99960.

## Across chips: placement is a fixed point, two float-derived scores are not

Of the 25 chunks, **24 produced byte-identical SAM on all three chips**, and Intel and AMD agree
on every byte (they run the same x86 binary). In chr4, Graviton4 differs from x86 on **9 of
662,096 records**. All of them are near chr4:49.7 Mb, the chr4 centromeric satellite:

- one read (`m64013_190322_2038…`, split into 5 alignments) has a supplementary at **MAPQ 26
  on Graviton4 and 25 on x86**. Its 4 sibling records differ only because their `SA:Z` tags quote
  that MAPQ;
- 4 secondary alignments at chr4:49,723,089–49,723,381 have `ms:i` (the DP max score) **one
  higher on x86** (6978 vs 6979).

Chromosome, position, CIGAR, NM, `AS` and flags are identical in every record. So is every count
and sum in the tables, including the MAPQ ≥ 30 gate (no record crosses 30). Placement and
alignment are a fixed point. MAPQ and `ms` come out of floating-point arithmetic, and on
near-ties in satellite DNA they can land one unit apart across ISAs. That is
expected of a value derived from floats, and nothing above asserts one. The mechanism (for
example FMA contraction in the aarch64 build) was not established.

## Pins

| | |
|---|---|
| reads | `s3://giab/data/NA12878/PacBio_SequelII_CCS_11kb/HG001_GRCh38/HG001_GRCh38.haplotag.RTG.trio.bam` (GIAB; its `md5sum.txt` lists `42b1d0d3…`, not re-checked, because the BAM is read over https and never stored), cut by `prep-genome.task.json` into 25 FASTQ chunks + pbmm2 placement tables, all pinned in `wg-pins.sha256` |
| reference | built: the 195 non-`_alt`/`HLA-`/`_decoy` sequences of RODA `1000genomes/technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa` (staged by [bwa-real](../bwa-real/README.md), sha256 `3b103f47…`) → `GRCh38_no_alt_analysis_set.fa`, sha256 `1f9044e2…`, 3,099,922,541 bp |
| truth | GIAB HG001 NISTv4.2.1 GRCh38 chr20 VCF + BED, as staged by [gatk4](../../recipes/gatk4/README.md) (`283d7fe5…`, `058ec9c0…`) |
| scripts | `genome.sh` `f8a11fe0…`, `align.sh` (the probe) `f57f7397…`, in `pins.sha256` and checked in-task |
| images | the recipe's `quay.io/aarchbio/minimap2@sha256:ef4a5fb7…` (arm64) / `quay.io/biocontainers/minimap2@sha256:f7e4a9a1…` (x86), plus its samtools and bcftools pins |

**Sizing.** `align.task.json` was the probe. It aligned the recipe's chr20 reads against the whole
genome on `m8g.4xlarge` at 16 threads: 169 s, a peak RSS of 19.7 GB, 15 Mbp/s, $0.051. That run is
what showed the whole sample was affordable. Total spend for this page: **$8.83** (prep $0.75,
three whole-genome runs $7.75, sort and truth $0.05, chr4 diagnosis $0.24, probe $0.05).
