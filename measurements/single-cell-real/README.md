# piscem + alevin-fry on a real workload — one whole 10x channel, Graviton4 and x86

> **A whole 10x channel — 116 million reads — goes from FASTQ to a filtered matrix in under
> 2 minutes of tool time on Graviton4.** At this size the bill is mostly boot, Docker and staging:
> about 75% of it is not piscem or alevin-fry.

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
```

There is no x86 run at the current pins: `quay.io/biocontainers` has no alevin-fry 0.18.3 image
yet, so the recipe records `gap_x86_64` and `spec_arch.py` refuses to resolve one. **AMD inherits
the gap** — it runs the same x86 pins — so there is no AMD row either (re-checked 2026-10-20: the
newest biocontainers tag is still `0.18.2--hd612981_0`). Both legs un-defer when 0.18.3 lands there.

Task 1 (piscem) verifies every staged byte against `pins-map.sha256`, indexes the cDNA and runs
`map-sc -g "1{b[16]u[10]x:}2{r:}"`. Task 2 (alevin-fry) builds three matrices from that RAD
file. **raw** comes from the 10x v2 permit list at the default `--min-reads 10`. **knee**
comes from `--knee-distance`. **filtered** takes the knee's cells, quantified from the *raw*
collation with `--quant-subset`, which is Cell Ranger's raw→filtered shape. Scripts:
[`map.sh`](map.sh), [`quant.sh`](quant.sh). Both are staged as pinned files.

## Results

16 threads on `m8g.4xlarge`, piscem 0.23.0 and alevin-fry 0.18.3 (2026-10-17):

| chip | instance | tool wall (index + map + quant) | billed (2 tasks) | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | **115 s** (35 + 65 + 15) | 452 s | 3.81 GiB | 0.7181 | **0.0229** | **0.0902** |
| Intel (8th gen), alevin-fry **0.9.0** | `m8i.4xlarge` | 149 s (39 + 79 + 31) | 455 s | 3.56 GiB | 0.8467 | 0.0350 | 0.1070 |

- **The Intel row is the 2026-10-13 run on alevin-fry 0.9.0 and was not re-run.** bioconda's
  x86 image of 0.18.3 does not exist yet. Its mapping phases are unchanged by the repin (piscem
  is the same pin), so Graviton4 mapping faster (65 s against 79 s) still stands. The quant
  column does not compare.
- **alevin-fry 0.18.3 quantifies in 15 s where 0.9.0 took 35 s** on the same Graviton4 box and
  the same RAD bytes. Its peak process memory is 1.30 GiB against 2.53 GiB. The earlier
  measurement in this directory (2026-10-13, 0.9.0) is in git history.
- **Three-quarters of the bill is overhead (75%).** From spawn's phase markers: each task spends
  about 14 s booting, 32 s installing Docker and 3–5 s pulling the image. The 8.3 GB of FASTQ
  stages in in 22 s. The in-task pin and identity checks take 28–30 s, and the box then idles
  55–75 s before the self-termination tick. That overhead
  is flat per task, so **per-sample cost falls as you put more channels on one box**. More cores
  would buy almost nothing.
- **The tool's memory is small, and staging sets the box size.** Peak process memory is
  3.8 GiB (the index build). But the task-2 tmpfs holds the 6.1 GB RAD plus collated RAD and
  matrices, 15.1 GiB at peak, so 64 GiB (32 GiB of `/tmp`) is what fits. This is
  [the staging-footprint rule](../../patterns/sizing.md).
- n = 1. piscem's phases reproduced the 0.9.0-era run to within 2 s (build 35 vs 37, map 65 vs
  66). **These timings are not compute cost.**

## What held at scale, on both chips

| identity | assertion | observed (both chips) |
|---|---|---|
| input fully read | piscem `num_reads` == FASTQ records (R1 == R2) | **116,459,032** == 116,459,032 |
| records cross the tool boundary | alevin-fry `observed` == piscem `num_mapped` | **86,550,564** == 86,550,564 |
| collation → quant | records quantified == Σ featureDump `MappedReads` (raw, knee, filtered) | **81,682,721 / 72,049,675 / 71,318,584**, exact |
| the read funnel only narrows | mapped ≥ orientation-consistent ≥ raw ≥ filtered | 86.55 M ≥ 83.78 M ≥ 81.68 M ≥ 71.32 M |
| piscem's unmapped file | header `1 1 3`, whole `(u32, u32)` records; mapped + unmapped ≤ reads | 86,550,564 + **29,906,218** ≤ 116,459,032 (809,126 barcodes) |
| unmapped reads reach `CorrectedReads` | per cell, `CorrectedReads − MappedReads` ≥ unmapped reads on that exact barcode; Σ over cells ≤ the file's total | **0 failures** in 130,375 + 1,814 + 1,814 cells; Σ 28,439,087 / 26,735,274 / 26,442,212 ≤ 29,906,218 |
| `MappingRate` | == `MappedReads / CorrectedReads` per cell (within 1e-6) | **all cells**, all three runs |
| per-cell totals | every cell's matrix row sum == its `DeduplicatedReads`, and nnz == `NumGenesExpressed` | **0 mismatches** in 130,375 + 1,814 + 1,814 cells |
| matrix total | Σ matrix == Σ `DeduplicatedReads` | raw **11,111,743**, filtered **9,043,752** |
| raw → filtered | every (cell, gene, count) in the filtered matrix is the raw matrix's, and every knee cell is in raw | **identical**, 1,814 of 1,814 |
| across the repin | sha256 of the sorted (cell, gene, count) triples | raw and filtered **identical** to 0.9.0's on both chips; knee differs (below) |

Observed, not asserted:

- **The knee matrix moved with the repin; raw and filtered did not.** The knee run still keeps
  1,814 cells, but it quantifies 72,049,675 records against 0.9.0's 72,054,428, and 9,134,357
  UMIs against 9,136,033. alevin-fry 0.18.0 replaced barcode correction with a deterministic
  `unique` policy (its release notes), and knee mode corrects against only 1,814 barcodes, which is
  where a policy change shows. The raw and filtered runs correct against the 10x permit list.
- **Exact-barcode unmapped reads are a floor, not the answer.** 27,796,930 of the 28,439,087
  unmapped reads credited to raw cells carry the cell's own barcode, and correction supplies the
  rest. The 2,250 reads that are neither mapped nor in the unmapped file presumably have an
  uncorrectable barcode (not checked).
- **The filtered cells hold 81.4% of the raw matrix's UMIs.** All **1,078** published cells are
  among the 1,814 the knee kept, and our per-cell UMI totals rank-correlate with the atlas's
  `n_counts` at **Spearman 0.927**. That atlas count came from Cell Ranger on mm10, so this is
  a different pipeline on a different reference: a sanity check, not a cross-validation.
- **`--knee-distance` and the raw→filtered cut give different counts for the same cells.**
  All 1,814 cells have more UMIs in the knee run (9,134,357 against 9,043,752). In knee mode,
  barcodes are corrected against just 1,814 cells, so more one-edit neighbours resolve
  uniquely. Both counts are correct for their own policy; pick one and say which.
- **The RAD files differ in bytes across chips (0.9.0-era runs), but their content is the same.**
  `map.rad` is 6,273,069,410 bytes on both chips, and the record order follows thread scheduling.
  The matrices still hashed identically. Mapping order is a path; the counts are a fixed point.

## `CorrectedReads` / `MappingRate`: wrong under alevin-fry 0.9.0, checked now

The first run of this measurement (2026-10-13) used alevin-fry **0.9.0**. That version reads
piscem's `unmapped_bc_count.bin` as headerless `(u64 barcode, u32 count)` pairs (`collate.rs`,
`correct_unmapped_counts`). piscem **0.23.0** writes a 3-byte header and then `(u32, u32)`
records. alevin-fry reads that layout from **0.12.0** on (libradicl's `UnmappedBcFormat`, with the
headerless form as a fallback). So under 0.9.0 the unmapped half of featureDump's
`CorrectedReads`, and the `MappingRate` derived from it, was decoded garbage that changed from run
to run on the same chip: raw `CorrectedReads` was 106,265,597 on one Graviton4 run and 81,682,721
(no unmapped reads at all) on the next. The matrix was unaffected.

Under 0.18.3, raw `CorrectedReads` is 110,121,808: 81,682,721 mapped plus 28,439,087 unmapped. The
check in the table above decodes piscem's file independently in `quant.sh`. The committed
0.9.0 arm64 result, `CorrectedReads == MappedReads` in every run, fails it: zero unmapped reads credited
against 27.8 M on the cells' own barcodes. The recipe's fixture checks the same decoder against
planted unmapped reads, and 0.9.0 fails that on AWS too.

## Pins (data tier: RODA + Ensembl + 10x's own repo)

| | |
|---|---|
| reads | `s3://czb-tabula-muris-senis/10x/FASTQs/10X_P5_13/` (RODA), copied same-region; sha256 in [`pins-map.sha256`](pins-map.sha256) |
| reference | Ensembl release 116 `Mus_musculus.GRCm39.cdna.all.fa.gz`, `6084ad63…` |
| permit list | `737K-august-2016.txt` from `10XGenomics/supernova@b82c3d8e`, `b0dda4b1…` (737,280 barcodes) |
| published cells | `tabula-muris-senis-full-metadata.csv` (RODA), `aa7e2d1b…` |
| images | the recipe's own: piscem 0.23.0 and alevin-fry 0.18.3 (`quay.io/aarchbio/*`). The x86 results are bioconda's piscem 0.23.0 and alevin-fry 0.9.0 |

Results: [`results/arm64/`](results/arm64/) (alevin-fry 0.18.3, 2026-10-17) and
[`results/x86_64/`](results/x86_64/) (alevin-fry 0.9.0, 2026-10-13). They hold `map.txt` and
`quant.txt` (every number above) plus the tools' own logs, and the arm64 `fry-logs.tgz` holds each
run's `featureDump.txt`.
