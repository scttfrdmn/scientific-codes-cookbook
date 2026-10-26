# vg Giraffe on a real workload: HG002 against the HPRC chr20 pangenome, three chips

> **Every one of HG002's 18.25 million chr20 Illumina reads, mapped by Giraffe to the HPRC v1.1
> chr20 graph that this build of vg indexed on the box.** Where Giraffe and novoalign are both
> confident (17.49M reads), 99.80% put the read's first base within 5 bp of each other. The
> alignments are byte-identical on Graviton4, Intel and AMD, though the index files are not.
> Graviton4 is cheapest per result, AMD maps fastest.

Workload: **one whole chromosome of one sample against a published human pangenome.** Giraffe
parallelises by read and a production run maps by chromosome or whole genome. A chr20 graph is
the unit HPRC itself publishes per chromosome.
- **Graph:** HPRC release 1 (v1.1) minigraph-cactus, GRCh38-referenced, chr20, the `d9` graph,
  which is the one HPRC builds for short-read mapping with Giraffe. It holds 44 HPRC samples plus
  GRCh38 and CHM13: 1,361,529 nodes, 1,592,700 edges and 65.2 Mbp of sequence, in 1,019,573 path
  fragments. **HG002 is held out of v1.1**, so this maps a sample the graph has never seen. That
  is the normal use, and the task asserts it.
- **Reads:** NIST's HG002 Illumina HiSeq 2×250 novoalign GRCh38 BAM, chr20 slice, already staged
  by [phasing-real](../phasing-real/README.md). That is 9,127,222 pairs (mean read 248.7 bp), at 69×.
  The 34,759 reads whose mate novoalign placed off chr20 are set aside.
- **Linear reference for the cross-check:** novoalign's placement of the same reads, from the same
  BAM.

**Data tier: RODA.** The graph is a server-side copy of
`s3://human-pangenomics/pangenomes/freeze/freeze1/minigraph-cactus/hprc-v1.1-mc-grch38/hprc-v1.1-mc-grch38.chroms/chr20.d9.vg`
at S3 version `0wTd0vbZPssWXgkhAVEc94YeX8C1DHHo`. The reads come from GIAB's md5-checked BAM via
phasing-real. Every staged byte, scripts included, is in [`pins.sha256`](pins.sha256), and each
task checks its inputs against it before it runs.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/pangenome-real/stage-inputs.sh "$B"     # graph RODA -> bucket, then 00-prep (samtools)
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py pangenome $a "$B" "$(date +%s)" \
    measurements/pangenome-real/01-giraffe.task.json)"
done
bash measurements/pangenome-real/collect.sh "$B" <nonce>
```

[`giraffe.sh`](giraffe.sh) is the recipe's tool run at full scale:

```sh
vg convert -f chr20.d9.vg > chr20.gfa
vg autoindex --workflow giraffe -g chr20.gfa -p idx -t 16          # GBZ + distance + minimizer + zipcodes
vg giraffe -Z idx.giraffe.gbz -d idx.dist -m idx.shortread.withzip.min -z idx.shortread.zipcodes \
  -f r1.fq.gz -f r2.fq.gz -t 16 -o gaf > aln.gaf
vg surject -x idx.giraffe.gbz -n GRCh38 -G aln.gaf -i -t 16 -s     # onto GRCh38 chr20, for the cross-check
```

[`prep.sh`](prep.sh) (`samtools collate | samtools fastq`) writes the FASTQ pair and novoalign's
placements once. [`mkspec.py`](mkspec.py) writes both specs and pins the staged scripts.

## Results

16 threads on an `m8*.4xlarge` (16 vCPU, 64 GiB) of each chip. Recipe images: vg 1.76.1 from
aarch.bio on Graviton4 and from bioconda on both x86 chips. All three read the same staged bytes:

| chip | instance | index | **giraffe** | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | 423 s | 246 s | 669 s | 996 s | 3.2 GiB | 0.7181 | **0.133** | **0.199** |
| Xeon 6975P-C | `m8i.4xlarge` | 334 s | 273 s | 607 s | 1,411 s | 3.2 GiB | 0.8467 | 0.143 | 0.332 |
| EPYC 9R45 | `m8a.4xlarge` | 385 s | **162 s** | **547 s** | 1,176 s | 3.2 GiB | 0.9738 | 0.148 | 0.318 |

A *result* is one sample's chromosome indexed, mapped and checked. *Index* is `vg convert` plus
`vg autoindex`. *Tool wall* is index plus Giraffe. *Billed* is spawn's task window, wrapper start to stage-out done, at
spawn's printed price. *Peak RSS* is the largest single process (`VmHWM`), which was autoindex on
every chip. Giraffe peaked at 2.0 GiB. Threads per core: 1 on Graviton4 and AMD, 2 on Intel (8 cores).

- **Graviton4 is cheapest per result, on compute and on billed.** AMD runs the whole job 1.22×
  faster but costs 36% more per hour. Intel indexes fastest and maps slowest.
- **For a cohort, the index is paid once and Giraffe is the per-sample cost.** Mapping alone ran
  at 37,100 pairs/s on Graviton4 ($0.049 per sample), 56,300 on AMD ($0.044) and 33,400 on Intel
  ($0.064). For mapping many samples against one index, AMD is cheapest per sample, by 11%.
- **The box is sized by the checks, not by vg.** vg never passed 3.2 GiB, so the tools alone would
  fit a 16 GiB box. The 64 GiB is for hashing the sorted 9.3 GB GAF: the x86 images' BusyBox `sort`
  holds it all in memory.
- **The x86 billed windows are long because of BusyBox `awk`.** The streamed checks took
  60 + 40 s on Graviton4 and 256 + 209 s on Intel. The `vg surject` time (41 / 117 / 67 s) is piped
  into that same `awk`, so it is not a clean tool timing on x86.
- n = 1 per cell. Giraffe is deterministic: the sorted GAF was identical on four Graviton4 runs
  and on all three chips.

## The checks, at scale, on all three chips

| observable | assertion | observed (every chip) |
|---|---|---|
| inputs | sha256 **==** `pins.sha256`, scripts included; the BAM **==** phasing-real's pin | yes |
| held-out sample | no HG002 path in the graph | 46 samples, none HG002 |
| **reference path** | the graph's one `GRCh38#chr20` path, normalised, has sha256 **==** `chr20.fa`'s | `24427012…`, the recipe's own value |
| FASTQ conservation | 2 × pairs + singletons **==** primary records in the BAM; mates pair by name | 18,289,203 |
| every read written | GAF records **==** 2 × pairs; mates adjacent; one name per pair | 18,254,444 |
| **`cs:Z` arithmetic** | in every mapped record, the difference string consumes exactly the query span (cols 3–4) and the target span (cols 8–9) | 0 exceptions in 18,226,925 |
| path bounds | every target span lies inside its path | 0 exceptions |
| surjection | primary SAM records **==** 2 × pairs | 18,254,444 |
| join accounting | reads joined to novoalign's record **==** 2 × pairs − surject's mate-less records | 18,178,130 = 18,254,444 − 76,314 |
| **vs novoalign** | where both have MAPQ ≥ 30, first base (POS less the leading clip) within 5 bp: **≥ 0.98** | **0.9980** (17,458,373 of 17,493,028) |
| across chips | sorted GAF and sorted surjected table identical | `b2f503f6…`, `a17b8ee5…` on all three |

- **The cross-check compares where each aligner put the read, not where it chose to clip.** On raw
  POS the agreement is 0.9861. The reads between that and 0.9980 are ones the two aligners clipped
  differently at the start, so the metric compares unclipped starts (the
  [cross-check rules](../../practices/cross-checks.md)). The 0.98 floor is
  the one minimap2↔bwa carries in [minimap2](../../recipes/minimap2/README.md). It sits two
  points under the observed value, and these are fixed points, so there is no run-to-run noise.
- **Giraffe places reads that novoalign does not.** 99.85% of reads map to the graph. 213,048 reads
  that novoalign left unmapped get MAPQ ≥ 30 from Giraffe. The graph carries 44 other genomes'
  sequence, so this is the expected direction. Nothing here can say whether those placements are
  right, so the number is reported, not asserted.
- **`vg surject -i` writes 76,314 records (0.42%) with no mate bit at all**: flag 0, 16 or 4, no
  0x1/0x40/0x80. Every read the SAM leaves unmapped, by Giraffe or by surjection off the GRCh38
  path, is among them, with its mate. Their mate cannot be read from the SAM, so they are counted, kept out
  of the join, and the join is held to that exact count. The GAF itself pairs every read: 9,127,222
  `fn:Z` and 9,127,222 `fp:Z` tags.
- **The recipe's identity holds on HPRC's own graph.** The recipe builds a graph from chr20 plus
  one VCF and checks that its reference path spells `chr20.fa` back. Here the same check, against
  the same 64,444,167 bases, passes on a graph built by minigraph-cactus from 90 haplotypes.

## Across chips

**The alignments are identical on all three chips; the index bytes are not.** The sorted GAF
(9.3 GB) and the sorted surjected table hash the same on Graviton4, Intel and AMD, so every count
and the concordance agree to the read. They are fixed points of the input bytes.

The index files differ, and that is reported, not asserted. The GBZ is stable per build: four
Graviton4 runs gave one hash and Intel and AMD another, and the files differ in size by 42 KB. The
distance, minimizer and zipcode indexes differ **run to run on one chip**: four Graviton4 builds
gave four hashes, with identical sizes. Those three are built multi-threaded. The alignments do
not change across any of it, so this is an invariance: Giraffe's output depends on what the index
says, not on how its bytes are laid out.

## Caveats

- One sample, one chromosome, one thread count. Neither Giraffe nor autoindex was swept.
- The graph is chr20 alone, so reads that belong elsewhere can only map here or not at all. The
  reads are novoalign's chr20 set, which keeps that small, but a whole-genome graph would be the
  production shape.
- The `d9` graph, not haplotype sampling (`vg haplotypes` from read k-mer counts, for which HPRC
  publishes `hprc-v1.1-mc-grch38.hapl`). Sampling needs a k-mer counter, another tool and task.
- The vg pins are the arm64 entry of a signed aarch.bio index (cosign-verified via the recipe) and
  bioconda's `linux/amd64` build of the same version, pinned by digest.
- Prices are on-demand us-west-2: spawn's `cost_estimate` divided by the billed window.
- Spend for this write-up: **$1.99**. That covers the three counted runs ($0.85), two prep runs
  ($0.07), a probe of the graph ($0.02), and six sizing and check-development runs on Graviton4
  ($1.06). Those runs found that GAF output carries two header lines, that a raw-POS comparison
  measures soft-clipping, and that `vg surject` drops mate bits.

## Files

`stage-inputs.sh`, `prep.sh` + `00-prep.task.json`, `giraffe.sh` + `01-giraffe.task.json`, `lib.sh`,
`mkspec.py`, `collect.sh`, `pins.sha256`. Evidence: `results/prep/` (FASTQ conservation),
`results/<chip>/` (checks, results, timings, the tools' own logs, the first 20 mate-less SAM
records, spawn's completion record), `results/crosschip.txt` (one line per chip, then every
compared value).
