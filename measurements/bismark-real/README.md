# Bismark, bwa-meth and MethylDackel on a whole RRBS sample: three chips

> **Every chip produces the same alignments and the same calls.** All 2,349,220 cytosines match
> byte for byte on Graviton4, Intel and AMD. AMD is 1.2× faster than Graviton4 overall and 11%
> dearer per sample billed. Intel is 1.2× slower and 35% dearer. bwa-meth is 71–74% of the compute on
> every chip, and on these 36 bp reads its default maps nothing.

Workload: **ENCODE `ENCFF000LYO`**, GM12878 RRBS replicate 1 (HudsonAlpha, [ENCSR000DFT](https://www.encodeproject.org/experiments/ENCSR000DFT/)),
the **whole** run: 42,372,813 single-end 36 bp GAIIx reads, against all of GRCh38 (the 195-sequence
no-alt analysis set [minimap2-real](../minimap2-real/README.md) staged). RRBS is sequenced one
library per sample at about this depth, and GM12878 is the reference cell line other methylation
studies use, so this is the size a researcher aligns. The recipe's fixture is 30 reads over three
planted CpGs.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/bismark-real/stage-inputs.sh "$B"      # RODA -> bucket, trim, both indices, once
for a in arm64 x86_64 amd; do
  for s in $(python3 scripts/spec_arch.py bismark $a "$B" "$(date +%s)" \
      measurements/bismark-real/0{1-bismark,2-sort,3-bwameth,4-call}.task.json); do
    spawn task run --spec "$s" --wait || break
  done
done
bash measurements/bismark-real/collect.sh "$B"           # results/<chip>/, results/compare.txt
```

Four tasks per chip, one tool each, with S3 between them. Each runs a script pinned by sha256, as
are all inputs (`pins.sha256`):

```sh
bismark align --genome genome --single_end ENCFF000LYO.trimmed.fastq.gz -p 8      # align-bismark.sh
bismark extract -s --comprehensive --bedGraph --gzip ENCFF000LYO.trimmed_bismark_bt2.bam
samtools sort -@ 8 -m 1G -o bismark.sorted.bam ...bismark_bt2.bam                 # sort.sh
bwameth.py --reference ref.fa -t 16 -T20 -K20000000 ENCFF000LYO.trimmed.fastq.gz  # align-bwameth.sh
MethylDackel extract -q 0 -p 1 -@ 16 GRCh38.fa bismark.sorted.bam                  # call.sh
```

Staging runs once, on arm64 (`00-prep-*`). `prep-trim.sh` converts the archive's Illumina 1.5
encoding (Phred+64, `.` for no-call) to Phred+33 and trims with Trim Galore's defaults through
[cutadapt](../../recipes/cutadapt/README.md) (Q20, the adapter, reads under 20 bp dropped): 31,569,432
reads kept. `bismark prepare` took 1,234 s on `m8g.4xlarge` ($0.25). `bwameth.py index` took
6,516 s on `r8g.2xlarge` ($0.86), because `bwa index` is single-threaded.

## Results

16 vCPU (`m8*.4xlarge`; `m8*.2xlarge` for the sort), the same staged bytes, the same thread counts.
aarch.bio's images on Graviton4, bioconda's on the other two:

| chip | Bismark align + extract | bwa-meth align + sort | tool wall | billed | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | 282 + 34 s | 818 + 14 s | 1,166 s | 2,169 s | 0.7181 | **0.2318** | **0.4186** |
| Xeon 6975P-C | 326 + 29 s | 1,048 + 14 s | 1,439 s | 2,481 s | 0.8467 | 0.3374 | 0.5655 |
| EPYC 9R45 | **220 + 24 s** | **715 + 10 s** | **981 s** | **1,784 s** | 0.9738 | 0.2647 | 0.4655 |

| chip | cores × threads | Bismark busy cores | bwa-meth busy cores | peak RSS: Bismark `-p 8` / `--multicore 4` / bwa-meth |
|---|---|---|---|---|
| Graviton4 | 16 × 1 | 14.9 | 15.7 | 9.8 / 29.3 / 15.4 GiB |
| Xeon 6975P-C | 8 × 2 | 15.1 | 15.8 | 9.8 / 29.3 / 15.4 GiB |
| EPYC 9R45 | 16 × 1 | 15.0 | 15.8 | 9.8 / 29.3 / 15.4 GiB |

A result is one whole sample through both aligners and both callers. The tool wall is the main
path: sort and the three MethylDackel calls (5–13 s) are included. Billed is all four tasks,
Started to Ended, so it also covers the check that reruns Bismark with `--multicore 4 -p 2`
(389 / 426 / 303 s).

- **AMD is 1.30× Graviton4 on Bismark (bowtie2) and 1.15× on bwa-meth.** At 36% more per hour,
  that makes it 14% dearer in compute and 11% dearer billed. **Intel is 0.89× and 0.78×**, and
  costs 46% more in compute and 35% more billed.
- **bwa-meth is 71–74% of every chip's compute** (Graviton4: 832 of 1,166 s). bwa-meth aligns to a
  6.2 Gbp converted reference, and its Python read conversion and post-processing sit in the
  pipe. Bismark aligns the same reads in under half the time. If one aligner is enough, Bismark's
  align and extract cost $0.063 per sample on Graviton4.
- **`-p 8` beats `--multicore 4 -p 2` on every chip:** `--multicore` takes 31–38% longer and uses
  three times the memory (9.8 against 29.3 GiB). Both modes give identical alignments and calls. Use `-p`.
- **Billed minus tool wall is 800–1,040 s per chip**, 42–46% of the bill. 325–455 s of that is
  the `--multicore` check. The other 480–590 s is boot, image pulls and staging the 7.8 GB Bismark
  index and the 11 GB bwa-meth index, once per task. For a cohort, align many samples per task.
- **Peak RSS is the same on all three chips** for every step. It is set by the index and the
  batch, not the chip.
- n = 1 per cell. Every output is byte-identical across chips (below), so a repeat would change
  the timings and nothing else.

## What held at scale, on all three chips

| claim | why it must hold | observed |
|---|---|---|
| reads analysed == reads in | nothing dropped before alignment | 31,569,432 |
| unique + no alignment + ambiguous == analysed | Bismark's accounting | 14,419,563 + 5,681,768 + 11,468,101 |
| call strings extracted == unique − discarded | every written alignment reaches the extractor | 14,419,557 = 14,419,563 − 6 |
| BAM records == unique − discarded, all primary | the BAM is the alignment set | 14,419,557 |
| per context, meth / unmeth: align report == splitting report | **conservation** between two subcommands | CpG 10,069,752 / 36,647,830; CHG 237,729 / 38,609,899; CHH 474,802 / 72,883,590 |
| C's analysed: align == extract | conservation | 158,923,602 |
| Σ coverage file == CpG totals | the bedGraph step conserves calls | yes |
| `-p 8` == `--multicore 4 -p 2` | parallel mode does not move an alignment or a call | alignments and coverage identical |
| **Bismark extractor == MethylDackel, same BAM** | **two independent callers, like with like** | **2,349,220 of 2,349,220 cytosines exact**; 46,717,582 calls each |
| bwa-meth: one primary record per read, no `XM` tags | the aligner reports every read | 31,569,432; `XM` 0 |
| **Bismark vs bwa-meth, MethylDackel on both, MAPQ ≥ 10, ≥ 10 calls in both** | mean \|Δβ\| below what two independent samples at those depths would show | **0.0102 vs 0.0332** at 748,470 CpGs; r = 0.9975 |

**The caller check is exact because the modes match.** MethylDackel runs with `-q 0` (Bismark has
already kept only unique alignments) and `-p 1` (the lowest base-quality floor it accepts; every
base here is Q2 or better). Its 0-based bedGraph is shifted to Bismark's 1-based C before the join,
as in the [recipe](../../recipes/bismark/README.md). With the modes matched, the two callers agree
on every cytosine, which is the [like-with-like](../../practices/cross-checks.md) standard.

**The aligner check is a bound, not an identity, and the bound comes from the data.** The two
aligners place different reads, so per-site counts cannot match. They read the same molecules,
though, so they must agree better than two independent samples of each site would. That baseline
is E|X−Y| for two binomial proportions at each site's two depths. The aligners came in at 31% of
it. Only 0.10% of sites differ by more than 0.25. Mean β is 0.2347 against 0.2349. The MAPQ ≥ 10
gate applies to both BAMs, because 2,106,416 of Bismark's "unique best" hits (15%) carry MAPQ
below 10.

Observed, not asserted:
- **CHH methylation 0.65%, CHG 0.61%**: bisulfite conversion is at least 99.35%, as expected for
  a somatic cell line. CpG methylation is 21.6% overall, which is typical for RRBS because MspI
  fragments concentrate on CpG islands.
- **Mapping efficiency is 45.7%.** 36% of reads are ambiguous: a 20–36 bp read in a three-letter
  alphabet often has two equally good homes. That is a property of 2011 read lengths, not of the
  aligner.
- Trimming kept 74.5% of reads. The GAIIx Q2 "B-tails" go at Q20, and only 0.8% of reads carried
  adapter. Trim Galore's `--rrbs` 2 bp rule acts only on adapter-trimmed reads, so it was not
  applied.

## Across chips: what is a fixed point and what is a path

**Nothing differs.** The sorted alignment records from both aligners (`QNAME`–`CIGAR` plus `SEQ`),
Bismark's coverage file, and all three MethylDackel bedGraphs have identical sha256 on all three
chips. So do Bismark's per-context totals, bwa-meth's mapped and MAPQ ≥ 10 counts, and the aligner
statistics to six decimals (`results/compare.txt`). The pipeline is integer alignment and counting,
with no BLAS, so the [OpenBLAS kernel question](../../patterns/cross-architecture.md) does not
arise. bowtie2 seeds its randomness from each read, and `-K` fixes bwa's batching, so thread count
does not move the result either.

**One capability differs: `MethylDackel perRead` does not run on ARM.** `perRead.c` stores
getopt's return in a `char` and loops while it is `>= 0`. `char` is unsigned on aarch64, so getopt's
end-of-options `-1` becomes 255, and every invocation exits 1 with `Invalid option '�'` before
reading the BAM. The same call on chr22 wrote 370,665 per-read rows on Intel and AMD. `extract`
declares the variable as `int` and is unaffected. This is still unfixed on MethylDackel `master`.

## Caveats

- **bwa-meth maps nothing on reads under 40 bp unless you lower `-T`, and it does not warn.** It
  calls `bwa mem -T 40`, and a 36 bp read scores at most 36. On the first 100,000 reads the
  default mapped **0**, and `-T20` mapped 99,138. bwa-meth forwards unknown flags to `bwa mem`, and
  bwa's last `-T` wins. The flag must be one token (`-T20`): as two tokens, bwa-meth's argument
  parser takes `20` as a FASTQ.
- **Set bwa's batch size (`-K`) for short reads.** The default is 10 Mbp per thread. A 20 Mbp batch
  peaked at 15.4 GiB, and the probe's single ~69 Mbp batch at 24.9 GiB. That is about 0.19 GiB per Mbp above the
  11 GiB index. At 16 threads the default batch projects to ~42 GiB, beside ~12 GiB of staged files
  in the tmpfs on a 62 GiB box. The first whole run without `-K` stalled until its 2 h TTL and left
  no log. That cause is consistent with the numbers but was not observed directly.
- Bismark's report labels a Rust-suite run `version: v0.25.1` (the Perl format it reproduces);
  `bismark --version` gives v3.1.0.
- One sample. Per-read cost and the index-staging overhead are what transfer.
- Prices are on-demand us-west-2, as spawn printed them for these instance types.
