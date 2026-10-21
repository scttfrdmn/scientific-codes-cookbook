# Scanpy on a real workload — 161,764 PBMCs, Graviton4, Intel and AMD

> **The whole standard pipeline on 162k cells is under five minutes on every chip, and half of it
> is `rank_genes_groups`.** It is the one step that scales worse than linearly, about 3× per
> doubling of cells. Graviton4 is 1.28× faster per run than Intel and 34% cheaper per result; AMD is
> fastest (1.38× Graviton4) and, at a higher hourly price, ties Graviton4 per result. Library sizes,
> normalisation and the HVG list are identical across chips; the clustering is a path.

Workload: **the complete Hao et al. 2021 PBMC dataset** (the Seurat v4 multimodal reference,
CELLxGENE Census dataset `ed5d841d-6346-47d4-ab2f-7119ad7e3a35`). That is 161,764 cells × 20,525
genes from 8 donors, 10x 3′ v3, with 353.5 M non-zero counts. It runs from the integer counts
through QC → `normalize_total` → `log1p` → HVG (2000) → scale → PCA (50) → kNN (k = 15, 40 PCs) →
Leiden → Louvain → UMAP → `rank_genes_groups` (Wilcoxon, all genes). The same pipeline also runs
on a nested ladder of 20k / 40k / 81k / 162k cells to show how each step scales.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/scanpy-real/stage-inputs.sh "$COOKBOOK_BUCKET"     # RODA -> bucket, server-side
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py scanpy $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/scanpy-real/hao2021.task.json)" --wait
done
python3 -I measurements/scanpy-real/crosschip.py <arm64 result/> <x86_64 result/>   # after copying both down
```

One task per chip verifies the h5ad and the three scripts against their sha256 pins. It then runs
`pipeline.py` twice in fresh processes: r1 is the ladder, with every check on the full rung, and
r2 is the full dataset alone. `compare.py` then requires r1 and r2 to be byte-identical.

## Results

8 threads, one `2xlarge` (64 GiB) per chip. The arm64 run uses the recipe's image digest; the two
x86 runs use its linux-64 env twin with the same scanpy, anndata, leidenalg, igraph and umap versions:

| chip | instance | tool wall (one result) | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | **328 s** | 992 s | 21.5 GiB | 0.4713 | **0.0429** | **0.1299** |
| Intel (Xeon 6975P-C) | `r8i.2xlarge` | 421 s | 1117 s | 22.0 GiB | 0.5557 | 0.0650 | 0.1724 |
| AMD (EPYC 9R45) | `r8a.2xlarge` | **238 s** | 679 s | 22.1 GiB | 0.6390 | **0.0422** | **0.1205** |

**Tool wall** is r2: one standalone process that loads the data, runs every step and checks it.
**Billed** runs from launch to the end of stage-out, so it also covers the ladder and the
replicate. Peak RSS is r2's `VmHWM`; the ladder process peaked at 24.6 / 25.0 GiB.

Per step, on the full dataset (r1, warm) and the growth from 81k to 162k cells:

| step | Graviton4 | ×2 cells | Intel | ×2 cells | AMD | ×2 cells |
|---|---|---|---|---|---|---|
| QC + filters | 7.2 s | 1.71× | 14.0 s | 1.98× | 6.0 s | 2.00× |
| normalize + log1p + HVG + scale | 5.8 s | — | 7.7 s | — | 3.2 s | — |
| PCA (arpack, 50) | 10.0 s | 2.01× | 21.6 s | 2.02× | 21.5 s | 2.01× |
| neighbors | 2.7 s | 1.63× | 3.0 s | 1.69× | 2.1 s | 1.54× |
| Leiden | 5.8 s | 2.33× | 4.1 s | 2.48× | 4.0 s | 2.31× |
| Louvain | 13.7 s | 2.45× | 13.9 s | 3.44× | 15.6 s | 3.10× |
| UMAP | 89.9 s | 2.05× | 76.7 s | 1.94× | 60.7 s | 2.24× |
| **`rank_genes_groups` (Wilcoxon)** | **140.4 s** | **2.99×** | **213.5 s** | **3.27×** | **97.6 s** | **3.14×** |
| total | 275.4 s | 2.44× | 354.4 s | 2.65× | 210.6 s | 2.58× |

- **The marker test is the bottleneck, and it gets worse with size.** It takes 35% of the
  pipeline at 40k cells on Graviton4 and 51% at 162k (42% → 60% on x86), because it is the only
  step that grows faster than the cell count. UMAP is second and linear. Everything from QC to
  clustering takes about 45 s at 162k cells.
- **Graviton4 wins on the bottleneck.** It runs `rank_genes_groups` in 140 s against 214 s on x86,
  and PCA and QC in half the time. x86 is faster at UMAP (77 vs 90 s) and Leiden. The total is
  1.28× in Graviton4's favour, with a 15% lower hourly price on top.
- **62–67% of the bill is not the result.** Boot, Docker and staging take about 2.5 min
  on arm64. The x86 run also pays for installing the env twin. The ladder and the replicate are
  the rest. One production run on this box would bill roughly 328 s plus 2.5 min.
- **AMD is fastest on every step but PCA and Louvain**, and fastest on the bottleneck (98 s).
  Its PCA takes 21.5 s, the same as Intel's and twice Graviton4's: both x86 chips run OpenBLAS's
  `Cooperlake` kernel. At a 36% higher hourly price than Graviton4 it lands 2% cheaper in compute
  and 7% cheaper billed. Its 8 vCPUs are full cores (`threads_per_core` 1); Intel's are 4 × 2.
  The AMD leg ran under spawn 0.139.1, which installs Docker during stage-in, so its billed window
  is not comparable with the others' on overhead.
- **Memory decides the box, not cores.** A single run peaks at 21.5 GiB, and the staged 2.67 GB
  h5ad sits in tmpfs as well. A 32 GiB `m8g.2xlarge` would probably hold it with about 8 GiB to
  spare, but that size was not run. The 64 GiB box is the measured one.
- **The first `neighbors` call in a process includes about 15 s of numba compilation.** It took
  17.5 s in r2 and 2.7 s in r1's warm full rung, and the 20k rung absorbs it in r1. A one-off
  run pays it once.
- n = 1 per chip. Every step except Louvain is byte-deterministic within a chip (below), so each
  timing is of one fixed computation.

## What held at scale, on all three chips

| observable | assertion | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| staged h5ad | sha256 `c55594bd…a29` | ok | ok | ok |
| raw dimensions | exactly 161,764 × 20,525, 353,548,607 non-zero, positive integers | ok | ok | ok |
| per-cell library sizes | integer row sums; Σ = 1,293,147,637 | identical file across chips | | |
| `normalize_total` | every cell sums to 1e4, < 1e-5 (float32) | 8.152e-08 | 8.152e-08 | 8.152e-08 |
| sparsity pattern | `indptr`/`indices` unchanged by normalisation | identical | identical | identical |
| one factor per cell | `X·lib/1e4` recovers every raw count, rel < 1e-6 | 1.162e-07 | 1.162e-07 | 1.162e-07 |
| `log1p` inverts | Σ `expm1` = 1e4 per cell, < 1e-5 | 3.009e-07 | 3.306e-07 | 3.306e-07 |
| **markers → published lineage** | CD3D, LYZ, MS4A1, NKG7 peak in 4 distinct Leiden clusters, each majority T / Mono / B / NK in the authors' `celltype.l1` | 70.6% CD8 T, 98.9% Mono, 100% B, 99.7% NK | 71.4%, 99.0%, 100%, 99.7% | 70.2%, 99.0%, 100%, 99.7% |
| **determinism, same box** | libsize, HVGs, PCA, UMAP, Leiden labels, top-25 markers byte-identical across two fresh processes | identical | identical | identical |

The lineage check is against an annotation Hao et al. built from RNA **and** 228 surface proteins
(WNN). It does not restate an RNA clustering. Reported, not asserted: 94.65% / 94.77% / 94.90%
of cells (Graviton4 / Intel / AMD) sit in a cluster whose majority `celltype.l1` is their own, and
ARI against `celltype.l2` is 0.522 / 0.530 / 0.526. The authors' own `nCount_RNA` was computed before CELLxGENE curation, so it matches
our library size exactly in only 3,512 cells, differing by up to 231 counts. It is reported, not
used as the identity.

QC (`min_genes=200`, `pct_counts_mt < 20`, `min_cells=3`) removes nothing here. The deposit is
the authors' filtered matrix, so those steps are timed but select every cell.

## Across chips: fixed point to PCA, path after it

From `crosschip.py` ([output](results/crosschip.txt)):

- **Fixed:** the library-size file and the 2000-gene HVG list (members *and* order) are
  byte-identical. PCA agrees to **7.6e-6** in magnitude (float32 arpack; signs are arbitrary per
  axis). The normalisation identities match to the printed digit, except the `log1p` round-trip
  (3.0 vs 3.3e-07), where libm's `expm1` differs in the last bits.
- **Path:** the kNN graph inherits PCA's last bits, and community detection amplifies them. Leiden
  gives **27 clusters on Graviton4 and 28 on x86, with ARI 0.9599 across chips**. UMAP
  coordinates differ by up to 12.6 units. Both are reported, not asserted. The OpenBLAS kernels
  are `neoversev2` and `Cooperlake`, so this is the [OpenBLAS rule](../../CLAUDE.md) at work.
- **AMD** ([output](results/crosschip-amd.txt)) shares the fixed points: the library-size file and
  the HVG list are byte-identical to both other chips. PCA is **1.2e-7 from Intel** and 7.6e-6
  from Graviton4. Intel and AMD both select OpenBLAS `Cooperlake` yet are not bit-identical here,
  unlike [scipy-stack-real](../scipy-stack-real/README.md)'s solved grid and
  [scikit-learn-real](../scikit-learn-real/README.md)'s boosting probabilities; what separates
  them is not isolated. UMAP coordinates differ by up to 12.0 units from Intel's. Leiden: 28 clusters, ARI **0.9611** against Intel and 0.9577 against Graviton4.
  Louvain on one AMD box gave ARI 0.8916 between the two fresh processes.
- **Louvain is not pinned by its seed, at 162k cells too.** Two fresh processes on one box gave
  ARI **0.9133** (27 vs 28 clusters) on Graviton4 and **0.9349** on x86. Across chips it is
  0.9073, which is no wider than its own run-to-run spread. This is the recipe's pbmc3k finding,
  repeated at 60× the cells.

## Pins

| | |
|---|---|
| data | CELLxGENE Census **LTS 2025-01-30** (`do_not_delete`), `h5ads/ed5d841d-6346-47d4-ab2f-7119ad7e3a35.h5ad`: 2,665,741,119 bytes, ETag `a7356ed7…-318`, sha256 `c55594bd58d86624f9d55626f841cb97aabf401e2ffed918933df214c693aa29`. Dataset version `4078abd1…`; Hao et al., *Cell* 2021, doi:10.1016/j.cell.2021.04.048 |
| tier | RODA (`s3://cellxgene-census-public-us-west-2`), copied server-side to the bucket |
| scripts | `pipeline.py`, `compare.py`, `run.sh`, each pinned by sha256 in `hao2021.task.json` |
| image | the recipe's `single-cell@sha256:14d4af5b…` (scanpy 1.12.4, anndata 0.13.4, leidenalg 0.12.0, igraph 1.0.0, umap-learn 0.5.12, pynndescent 0.6.0, numba 0.68.0, OpenBLAS 0.3.34); x86 via `envs-x86_64/single-cell-957f8c9c38e0` |

**Why this dataset:** 162k cells is the size of a whole multi-donor study, and the scale at which
Scanpy's step costs separate. The two sibling measurements use one 10x lane each
([seurat-real](../seurat-real/README.md), 11k cells; [single-cell-real](../single-cell-real/README.md),
one channel). Its counts are the unmodified integers. Its annotation came partly from protein, so
the marker check has an independent truth to land on.

## Caveats

The spend was $0.30 for Graviton4 and Intel, billed, and $0.12 for AMD. The AMD leg staged the
resolved command as a sha256-checked `task.sh`, byte for byte what `spec_arch.py` emitted plus a
non-failing CPU-topology print: spawn 0.139.1's wrapper leaves too little of EC2's 16 KiB user data
for it inline (spawn#1246). The x86 cgroup peak (30.4 GiB) also counts the env
twin's install. A per-step RSS was not recorded, only each process's peak.
