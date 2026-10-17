# Seurat on a real workload — 11,043 PBMCs, Graviton4 and x86

> **Clustering 11k cells takes one minute; finding their markers takes 70–83.** In this env
> Seurat's `FindAllMarkers` falls back to its R Wilcoxon test (no `presto`), and that one step is
> **98.5–98.8% of the workflow** on both chips. x86 is 1.19× faster at it, for the same cost per result.

Workload: the **complete** 10x Genomics `pbmc_10k_v3` filtered matrix (11,769 cells × 33,538
genes; 11,043 × 20,244 after QC), through the full standard workflow: QC → `LogNormalize` →
HVG → scale → PCA (40) → kNN (k = 10) → clustering (resolution 1.0) → `FindAllMarkers`. Same
matched design as [the recipe](../../recipes/seurat/README.md): Scanpy does QC and picks the 2000
HVGs, Seurat runs on exactly those cells, genes and features, and both partitions are compared.

## Run it

```sh
./stage-inputs.sh "$COOKBOOK_BUCKET"
spawn task run --spec "$(python3 ../../scripts/spec_arch.py seurat arm64 "$COOKBOOK_BUCKET" r1 pbmc10k.task.json)" --wait
spawn task run --spec "$(python3 ../../scripts/spec_arch.py seurat x86_64 "$COOKBOOK_BUCKET" r1 pbmc10k.task.json)" --wait
python3 -I crosschip.py arm64/labels.csv x86_64/labels.csv    # after copying both runs' labels.csv down
```

## Results

4 vCPU / 16 GiB on both chips, 4 threads pinned, same versions (arm64 image digest; the x86 run
is its linux-64 env twin):

| chip | instance | tool wall | of which `FindAllMarkers` | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.xlarge` | 5023 s | 4962 s (98.8%) | 5327 s | 4.73 GiB | 0.1795 | 0.2504 | 0.2656 |
| x86 (Intel) | `m8i.xlarge` | **4231 s** | 4169 s (98.5%) | 4427 s | 5.78 GiB | 0.2117 | **0.2488** | 0.2603 |

Tool wall is the one result (Scanpy leg + Seurat leg with markers). Billed is launch to terminate
and also carries the determinism replicate (~60 s per chip, both legs rerun without markers).

- **The answer to "which box" is "install presto".** Everything before markers — QC through
  clustering, both tools — is **57–58 s** on either chip. The remaining 70–83 minutes are one
  single-threaded R loop: three of the four vCPUs idle for 98% of the bill. Seurat's own message
  says so; conda has `r-presto` 1.0.0 for linux-aarch64 (bioconda) but this env does not carry it.
- **x86 is 1.19× faster on the serial Wilcoxon and 18% dearer per hour**, so compute $/result is a
  wash (0.2504 vs 0.2488). Neither chip earns its keep here; the fix is the algorithm, not the box.
- **Peak RSS 4.7–5.8 GiB**, so an `m8g.large` (8 GiB) would hold it — but with one core busy the
  saving is the idle cores, which is the same answer again.
- n = 1 per chip. The two legs that matter are byte-deterministic within a chip (below), so the
  timing is of one fixed computation, not of one draw.

## What held at scale, on both chips

| observable | assertion | Graviton4 | x86 |
|---|---|---|---|
| raw / post-QC dims | exact 11769 × 33538 → identical sets in both tools | 11043 × 20244 | 11043 × 20244 |
| **per-cell library size** | Seurat's `colSums` == Scanpy's, every cell, integer | 0 of 11043 differ, Σ 88,691,854 | same |
| Scanpy `normalize_total` | every cell sums to 1e4, < 1e-5 (float32) | 7.122e-08 | 7.122e-08 |
| Seurat `LogNormalize` | Σ expm1(data) = 1e4, < 1e-10 (double) | 7.749e-14 | 7.749e-14 |
| **determinism, same box** | QC lists, library sizes, HVGs, leiden and Seurat labels byte-identical across two fresh processes | identical | identical |
| markers | CD3D/LYZ/MS4A1/NKG7 each a significant (p_adj < 0.05) `FindAllMarkers` marker of the cluster it peaks in, four distinct clusters | 10, 0, 4, 6 | 10, 0, 4, 6 |
| marker separation | same four markers in four distinct clusters, all three partitions | pass | pass |
| Seurat ↔ Scanpy leiden | ARI ≥ 0.45 (the recipe's floor) | 0.7285 | 0.8439 |

These are fixed points — integer sums and normalisation identities — and they agree across chips
to the last printed digit.

## What differed across chips: the partitions (a path)

| partition | Graviton4 | x86 | ARI between chips |
|---|---|---|---|
| Seurat (Louvain, `random.seed = 0`) | 25 clusters | 26 | **0.9728** |
| Scanpy leiden (`random_state = 0`) | 24 | 22 | 0.8367 |
| Scanpy louvain (`random_state = 0`) | 21 | 20 | 0.8253 |

Both seeded partitions are exactly reproducible on one chip and different on the other, which is
the [OpenBLAS rule](../../CLAUDE.md) in practice: the processes load `neoversev2` and `Cooperlake`
kernels, PCA is large blocked linear algebra, and graph clustering amplifies the last bits into
membership. So the cross-chip ARI is reported, not asserted. Seurat's partition moves least
(0.97); the same cells under the same seed on the other chip change leiden more than switching
from Seurat to Scanpy does on x86.

**Scanpy's louvain is not pinned by its seed, at scale too.** Two fresh processes on the same box
gave ARI **0.8379** (21 vs 22 clusters) on Graviton4 and **0.8461** on x86 — the recipe's pbmc3k
finding, reproduced on 4× the cells. Every number involving louvain is one draw; its
within-chip spread is about as wide as its cross-chip one.

Cross-tool agreement also moved: Seurat ↔ leiden **0.7285 / 0.8439**, Seurat ↔ louvain
0.8492 / 0.8250. It is a property of two paths, so it does not transfer between chips with more
precision than about ±0.1 here.

## Pins

| | |
|---|---|
| matrix | `pbmc_10k_v3_filtered_feature_bc_matrix.tar.gz`, 10x Genomics Cell Ranger 3.0.0 release path, sha256 `060af9e4…48cf63`, verified before upload and again in-task |
| image | `quay.io/aarchsci/single-cell@sha256:1105adf7…` (Seurat 5.5.1, R 4.5.3, scanpy 1.12.4); x86 via `envs-x86_64/single-cell-ff275bd07c2f` |
| scripts | `scanpy_leg.py`, `seurat_leg.R`, `compare.py`, `run.sh`, each pinned by sha256 in the spec |

**Data tier: a versioned vendor release, not RODA.** The open-data single-cell holding (CZ
CELLxGENE Census) carries curated, re-annotated h5ad, not the Cell Ranger output a lab gets back
and feeds to `Read10X`. pbmc_10k_v3 is that output, unchanged at its URL since 2018-11-15, and the
10k-cell PBMC run both ecosystems' tutorials scale up to. 11k cells is a single 10x lane: the unit
a researcher actually clusters, and 4× the recipe's pbmc3k.

## Caveats

Peak RSS is each process's `VmHWM`; the x86 container's cgroup peak (9.53 GiB) also counts the env
twin's install, which arm64 does not do. The HVG list's cross-chip identity was not staged out;
its count (2000) and its overlap with Seurat's own `vst` selection (803 of 2000) match on both.
