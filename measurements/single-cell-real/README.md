# piscem + alevin-fry on a real workload — one whole 10x channel, on three chips

> **A whole 10x channel — 116 million reads — goes from FASTQ to a filtered matrix in under
> 2.5 minutes of tool time on Graviton4, Intel and AMD, with identical matrices on all three.**
> At this size the bill is mostly boot, Docker and staging: 67–75% of it is not piscem or alevin-fry.

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
# x86, from the same two specs: x86_64 (Intel m8i) or amd (AMD m8a)
bash ../../scripts/stage_env_x86.sh bioconda-piscem-0.23.0 "$COOKBOOK_BUCKET"
bash ../../scripts/stage_env_x86.sh bioconda-alevin-fry-0.18.3 "$COOKBOOK_BUCKET"
for s in $(python3 x86-specs.py x86_64 "$COOKBOOK_BUCKET" "$(date +%s)" --cost-limit 0.25); do
  spawn task run --spec "$s" --wait
done
```

**The x86 route is an exception to the sourcing rule, stated plainly:** `quay.io/biocontainers`
had no alevin-fry 0.18.3 image on 2026-10-24 (its newest tag is `0.18.2--hd612981_0`), so the x86
legs install **bioconda's own linux-64 builds** offline from committed locks,
[`bioconda-piscem-0.23.0`](../../envs-x86_64/bioconda-piscem-0.23.0.linux-64.lock) and
[`bioconda-alevin-fry-0.18.3`](../../envs-x86_64/bioconda-alevin-fry-0.18.3.linux-64.lock), into
the pinned micromamba image the aarch.science twins use. Each lock is the arm64 image's own package
set, read from its `conda-meta`: 7 and 5 packages, every version and build number the same (piscem
`h79c2db1_0`, alevin-fry `hd612981_0`), URL + sha256 per package. Still one tool per task.
[`x86-specs.py`](x86-specs.py) does the rewrite, because `scripts/spec_arch.py`'s env route covers
aarch.science images only. The recipe's own fixture still records `gap_x86_64`.

Task 1 (piscem) verifies every staged byte against `pins-map.sha256`, indexes the cDNA and runs
`map-sc -g "1{b[16]u[10]x:}2{r:}"`. Task 2 (alevin-fry) builds three matrices from that RAD
file. **raw** comes from the 10x v2 permit list at the default `--min-reads 10`. **knee**
comes from `--knee-distance`. **filtered** takes the knee's cells, quantified from the *raw*
collation with `--quant-subset`, which is Cell Ranger's raw→filtered shape. Scripts:
[`map.sh`](map.sh), [`quant.sh`](quant.sh). Both are staged as pinned files, the same bytes on
every chip.

## Results

16 threads on a 4xlarge, piscem 0.23.0 and alevin-fry 0.18.3 on every chip (Graviton4 2026-10-17,
Intel and AMD 2026-10-24):

| chip | instance | tool wall (index + map + quant) | billed (2 tasks) | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.4xlarge` | 115 s (35 + 65 + 15) | 452 s | 3.81 GiB | 0.7181 | **0.0229** | 0.0902 |
| Intel (Xeon 6975P-C) | `m8i.4xlarge` | 131 s (39 + 75 + 17) | 392 s | 3.44 GiB | 0.8467 | 0.0308 | 0.0922 |
| AMD (EPYC 9R45) | `m8a.4xlarge` | **85 s** (29 + 44 + 12) | 324 s | 3.41 GiB | 0.9738 | 0.0230 | **0.0876** |

- **AMD is fastest; Graviton4 and AMD tie on compute cost.** AMD's 16 vCPUs are 16 full cores
  (`lscpu`: 1 thread per core), Intel's are 8 cores × 2 SMT threads. piscem maps in 44 s on AMD,
  65 s on Graviton4 and 75 s on Intel. AMD's speed cancels its 36% higher hourly price: compute
  $/result 0.0230 against Graviton4's 0.0229. Intel costs a third more per result.
- **alevin-fry 0.18.3 quantifies in 15 s where 0.9.0 took 35 s** on the same Graviton4 box and
  RAD bytes (Intel: 17 s against 31 s), at half the peak memory (1.30 GiB against 2.53 GiB).
- **The billed column does not rank the chips.** It is EC2 launch to termination-initiated, and
  the self-termination tick alone moves it by about a minute per task (boxes idled 23–76 s
  after stage-out). The spread across chips is inside that noise.
- **Most of the bill is overhead: 75% on Graviton4, 67% on Intel, 74% on AMD.** From spawn's
  phase markers: each task spends about 11–14 s booting, 30–44 s installing Docker and 2–5 s
  pulling the image. The 8.3 GB of FASTQ stages in in 17–22 s, and the x86 env installs from
  its staged packages in under a second. That overhead is flat per task, so **per-sample cost
  falls as you put more channels on one box**. More cores would buy almost nothing.
- **The tool's memory is small, and staging sets the box size.** Peak process memory is
  3.4–3.8 GiB (the index build). But the task-2 tmpfs holds the 6.1 GB RAD plus collated RAD and
  matrices, 12.3–15.1 GiB at peak, so 64 GiB (32 GiB of `/tmp`) is what fits. This is
  [the staging-footprint rule](../../patterns/sizing.md).
- n = 1 per chip. Everything asserted below is deterministic and came out identical on all three,
  which is what makes one run per cell defensible. **These timings are not compute cost.**

## What held at scale

Every row passed on all three chips with the same value. Observed is Graviton4; Intel's and AMD's
`quant.txt` and `map.txt` differ from it only in wall times, memory samples and the `arch` label.

| identity | assertion | observed (Graviton4) |
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
| across chips | sha256 of the sorted (cell, gene, count) triples, Graviton4 vs Intel vs AMD | raw `903afd73…`, knee `159a2235…`, filtered `fff33213…`: **identical** on all three |
| across the repin | the same hashes against alevin-fry 0.9.0's (2026-10-13, Graviton4 and Intel) | raw and filtered **identical**; knee differs (below) |

Observed, not asserted:

- **The knee matrix moved with the repin; raw and filtered did not.** The knee run still keeps
  1,814 cells, but it quantifies 72,049,675 records against 0.9.0's 72,054,428, and 9,134,357
  UMIs against 9,136,033. alevin-fry 0.18.0 replaced barcode correction with a deterministic
  `unique` policy (its release notes), and knee mode corrects against only 1,814 barcodes, which is
  where a policy change shows. The raw and filtered runs correct against the 10x permit list.
  On 0.18.3 the knee run is the same on all three chips (72,049,675 records, triples
  `159a2235…`), so the 0.9.0 Intel run's different knee was the version, not the chip.
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
- **piscem's output bytes differ across chips; their content does not.** `map.rad` is
  6,273,069,410 bytes on all three, but its S3 ETags differ between Intel and AMD at the same part
  count (1,197), because record order follows thread scheduling. `unmapped_bc_count.bin` is
  17,778,267 bytes on both x86 boxes and 17,778,579 on Graviton4, 39 more 8-byte records, since
  threads flush partial per-barcode counts. All three decode to the same 809,126 barcodes and
  29,906,218 reads, and the matrices hash identically. Byte order and record splits are a path;
  the counts are a fixed point.

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
| images (arm64) | the recipe's own: piscem 0.23.0 and alevin-fry 0.18.3 (`quay.io/aarchbio/*`) |
| x86 (Intel, AMD) | bioconda's linux-64 piscem 0.23.0 `h79c2db1_0` and alevin-fry 0.18.3 `hd612981_0` with their arm64 images' runtime deps, from [`envs-x86_64/bioconda-*.linux-64.lock`](../../envs-x86_64/) (URL + sha256), installed offline into `mambaorg/micromamba@sha256:9a2239d0…` (amd64) |

Results: [`results/arm64/`](results/arm64/) (2026-10-17), [`results/x86_64/`](results/x86_64/)
and [`results/amd/`](results/amd/) (2026-10-24), all on alevin-fry 0.18.3. They hold `map.txt` and
`quant.txt` (every number above) plus the tools' own logs, and each `fry-logs.tgz` holds every
run's `featureDump.txt`. The superseded Intel 0.9.0 run (2026-10-13) is in git history.
