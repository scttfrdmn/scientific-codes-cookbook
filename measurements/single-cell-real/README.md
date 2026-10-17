# piscem + alevin-fry on a real workload — one whole 10x channel, Graviton4 and x86

> **A whole 10x channel — 116 million reads — goes from FASTQ to a filtered matrix in under
> 2.5 minutes of tool time, and the matrix is bit-identical on Graviton4 and Intel.** At this
> size the bill is mostly boot, Docker and staging: about 70% of it is not piscem or alevin-fry.

Workload: **one complete Tabula Muris Senis droplet channel**, `10X_P5_13` (1-month male mouse
lung, Chromium 3′ v2, both lanes): **116,459,032 read pairs**, 8.19 GB of R1+R2, mapped against
**all 319,954 transcripts** of Ensembl 116 mouse cDNA (36,130 genes). This is one 10x
library, exactly as one comes off the sequencer. A cohort is this task fanned out once per
channel, not a bigger single input. The published atlas kept **1,078** cells from this channel.

## Run it

```sh
export AWS_PROFILE=aws
./stage-inputs.sh "$COOKBOOK_BUCKET"                       # RODA reads + Ensembl cDNA + 10x v2 permit list
for s in 01-map 02-quant; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" $s.task.json > /tmp/$s.json
  spawn task run --spec /tmp/$s.json --wait
done
# x86: the same specs, rewritten from the recipe's own x86 pins
for s in $(python3 ../../scripts/spec_arch.py single-cell x86_64 "$COOKBOOK_BUCKET" r1 01-map.task.json 02-quant.task.json); do
  spawn task run --spec "$s" --wait
done
```

Task 1 (piscem) verifies every staged byte against `pins-map.sha256`, indexes the cDNA and runs
`map-sc -g "1{b[16]u[10]x:}2{r:}"`. Task 2 (alevin-fry) builds three matrices from that RAD
file. **raw** comes from the 10x v2 permit list at the default `--min-reads 10`. **knee**
comes from `--knee-distance`. **filtered** takes the knee's cells, quantified from the *raw*
collation with `--quant-subset`, which is Cell Ranger's raw→filtered shape. Scripts:
[`map.sh`](map.sh), [`quant.sh`](quant.sh). Both are staged as pinned files.

## Results

16 threads on a `4xlarge` of each chip, same versions, same input bytes:

| chip | instance | tool wall (index + map + quant) | billed (2 tasks) | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | **138 s** (37 + 66 + 35) | 452 s | 3.60 GiB | 0.7181 | **0.0275** | **0.0902** |
| Intel (8th gen) | `m8i.4xlarge` | 149 s (39 + 79 + 31) | 455 s | 3.56 GiB | 0.8467 | 0.0350 | 0.1070 |

- **Graviton4 is 21% cheaper per result in compute and 16% cheaper billed.** It maps faster
  (66 s against 79 s), and alevin-fry is slightly faster on Intel (31 s against 35 s). The
  index builds take the same time. Earlier runs on the same arm64 bytes reproduced each phase to
  within ±2 s (map 65–66 s, quant 34–36 s).
- **Two-thirds of the bill is overhead (69% arm64, 67% x86).** On Graviton4, each task spends
  60–80 s on boot, Docker install, image pull and staging. The 8.3 GB of FASTQ stages in
  19 s. Another 20–30 s goes to the in-task pin and identity checks, and 48–72 s to idling
  before the self-termination tick. That overhead is flat per task. So **per-sample cost falls
  as you put more channels on one box**, and more cores would buy almost nothing.
- **The tool's memory is small, and staging sets the box size.** Peak process memory is
  3.6 GiB (the index build). But the task-2 tmpfs holds the 6.1 GB RAD plus ~14 GB of
  collated RAD and matrices, so 64 GiB (32 GiB of `/tmp`) is what fits. A 32 GiB box would not.
  This is [the staging-footprint rule](../../patterns/sizing.md).
- n = 1 per cell. The tools are deterministic where it matters (the matrices below are
  identical across chips and across runs), so the timings compare like with like.
  **These timings are not compute cost.**

## What held at scale, on both chips

| identity | assertion | observed (both chips) |
|---|---|---|
| input fully read | piscem `num_reads` == FASTQ records (R1 == R2) | **116,459,032** == 116,459,032 |
| records cross the tool boundary | alevin-fry `observed` == piscem `num_mapped` | **86,550,564** == 86,550,564 |
| collation → quant | records quantified == Σ featureDump `MappedReads` (raw, knee, filtered) | **81,682,721 / 72,054,428 / 71,318,584**, exact |
| the read funnel only narrows | mapped ≥ orientation-consistent ≥ raw ≥ filtered | 86.55 M ≥ 83.78 M ≥ 81.68 M ≥ 71.32 M |
| per-cell totals | every cell's matrix row sum == its `DeduplicatedReads`, and nnz == `NumGenesExpressed` | **0 mismatches** in 130,375 + 1,814 + 1,814 cells |
| matrix total | Σ matrix == Σ `DeduplicatedReads` | raw **11,111,743**, filtered **9,043,752** |
| raw → filtered | every (cell, gene, count) in the filtered matrix is the raw matrix's, and every knee cell is in raw | **identical**, 1,814 of 1,814 |
| cross-chip fixed point | sha256 of the sorted (cell, gene, count) triples, all three matrices | **identical** on arm64 and x86_64 |

Observed, not asserted:

- **The filtered cells hold 81.4% of the raw matrix's UMIs.** All **1,078** published cells are
  among the 1,814 the knee kept, and our per-cell UMI totals rank-correlate with the atlas's
  `n_counts` at **Spearman 0.927**. That atlas count came from Cell Ranger on mm10, so this is
  a different pipeline on a different reference: a sanity check, not a cross-validation.
- **`--knee-distance` and the raw→filtered cut give different counts for the same cells.**
  All 1,814 cells have more UMIs in the knee run (9,136,033 against 9,043,752). In knee mode,
  barcodes are corrected against just 1,814 cells, so more one-edit neighbours resolve
  uniquely. Both counts are correct for their own policy; pick one and say which.
- **The RAD files differ in bytes across chips, but their content is the same.** `map.rad` is
  6,273,069,410 bytes on both chips, and the record order follows thread scheduling. The
  matrices still hash identically. Mapping order is a path; the counts are a fixed point.

## A field you should not read: `CorrectedReads` / `MappingRate`

alevin-fry **0.9.0** reads piscem's `unmapped_bc_count.bin` as headerless `(u64 barcode, u32
count)` pairs (`collate.rs`, `correct_unmapped_counts`). piscem **0.23.0** writes a newer
layout: a 3-byte header, then `(u32, u32)` records. So the unmapped half of featureDump's
`CorrectedReads`, and the `MappingRate` derived from it, is decoded garbage. Parsed the 0.9.0
way, the file's counts sum to ~1.5×10¹⁵. Parsed correctly, they sum to 29.9 M, which is the real
unmapped total. Because the garbage depends on record layout, it changes from run to run on the
*same* chip. The raw `CorrectedReads` was 106,265,597 on one Graviton4 run and 81,682,721 on
the next. Running x86 alevin-fry on the arm64 RAD reproduced the arm64 value exactly, so this
is about the RAD bytes, not the chip. **The matrix is unaffected**: mapped records go through a
different path, and every identity above holds. This pins pair a 2024 quantifier with a 2026
mapper; alevin-fry 0.18.3 exists on both aarch.bio and bioconda.

## Pins (data tier: RODA + Ensembl + 10x's own repo)

| | |
|---|---|
| reads | `s3://czb-tabula-muris-senis/10x/FASTQs/10X_P5_13/` (RODA), copied same-region; sha256 in [`pins-map.sha256`](pins-map.sha256) |
| reference | Ensembl release 116 `Mus_musculus.GRCm39.cdna.all.fa.gz`, `6084ad63…` |
| permit list | `737K-august-2016.txt` from `10XGenomics/supernova@b82c3d8e`, `b0dda4b1…` (737,280 barcodes) |
| published cells | `tabula-muris-senis-full-metadata.csv` (RODA), `aa7e2d1b…` |
| images | the recipe's own: piscem 0.23.0 and alevin-fry 0.9.0, `quay.io/aarchbio/*` on arm64 and `quay.io/biocontainers/*` on x86 |

Results: [`results/arm64/`](results/arm64/) and [`results/x86_64/`](results/x86_64/). They hold
`map.txt` and `quant.txt` (every number above) plus the tools' own logs.
