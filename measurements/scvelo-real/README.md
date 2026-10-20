# scVelo on a whole gastrulation atlas — Graviton4, Intel and AMD

> **scVelo's full workflow on 89,267 cells, through the dynamical model on all 1,138 velocity genes,
> takes under 10 minutes on Graviton4, and every identity holds on all three chips.** Graviton4 is the
> fastest and cheapest per result. The dynamical fit is the whole bill. It is per gene, so the worker
> count can't change it, and it is bit-identical within a chip. Across chips it is a path. On this
> env the default `scv.tl.velocity` mode raises, and velocity pseudotime can stall on one chip.

Workload: **the mouse gastrulation atlas exactly as scVelo distributes it** (`scv.datasets.gastrulation()`;
Pijuan-Sala et al., *Nature* 2019). That is 89,267 cells × 53,801 genes, from E6.5 to E8.5, with
1.05 G spliced and 63.7 M unspliced molecules. The run follows scVelo's tutorial order:
`filter_genes(min_shared_counts=20)` → `normalize_per_cell` → `log1p` + 2,000 HVGs → PCA (30) →
kNN (30) → `moments` → steady-state and stochastic velocity → `recover_dynamics` on every steady-state
velocity gene (16 workers) → dynamical velocity → `velocity_graph` → `velocity_pseudotime`.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/scvelo-real/stage-inputs.sh "$B"     # 4.69 GB, figshare -> bucket, fetched on AWS
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py scvelo $a "$B" "$(date +%s)" \
    measurements/scvelo-real/real.task.json)" --wait
done
bash measurements/scvelo-real/collect.sh "$B" <scratch dir>     # needs a python3 with numpy
```

[`real.py`](real.py) is the workload and its checks; [`real.sh`](real.sh) records the host and runs it.
The calls a user writes, with the env's three workarounds (each probed first and recorded):

```python
scv.pp.filter_genes(adata, min_shared_counts=20); scv.pp.normalize_per_cell(adata)
sc.pp.log1p(adata); sc.pp.highly_variable_genes(adata, n_top_genes=2000, subset=True)
sc.pp.pca(adata, n_comps=30, random_state=0); sc.pp.neighbors(adata, n_neighbors=30, random_state=0)
scv.pp.moments(adata, n_pcs=None, n_neighbors=None)
scv.tl.velocity(adata, mode="stochastic")        # needs the leastsq_generalized shim on numpy 2.5 (below)
scv.tl.recover_dynamics(adata, var_names=genes, t_max=False, n_jobs=16)   # the recipe's two pandas-3 workarounds
scv.tl.velocity(adata, mode="dynamical_residuals", vkey="dyn")
```

## Results

16 vCPU, `r8*.4xlarge`, 128 GiB, one per chip. The arm64 run uses the recipe's image digest; the x86
runs use the `single-cell` env's linux-64 twin (scVelo, scanpy, anndata, pandas and numpy at the same
versions). Same staged bytes, same seeds, same `n_jobs`:

| chip | instance | cores × threads | scVelo wall (one result) | billed | peak RSS (main / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.4xlarge` | 16 × 1 | **587 s** | 797 s | 37.8 / 54.3 GiB | 0.9426 | **0.154** | **0.209** |
| Xeon 6975P-C | `r8i.4xlarge` | 8 × 2 | 1078 s | 1192 s | 37.2 / 59.1 GiB | 1.1114 | 0.333 | 0.368 |
| EPYC 9R45 | `r8a.4xlarge` | 16 × 1 | 636 s | 717 s | 30.5 / 60.6 GiB | 1.2781 | 0.226 | 0.255 |

**scVelo wall** is the sum of the workflow's phases, from reading the h5ad to `velocity_graph`, with
every inline check. It excludes the `n_jobs=1` invariance refit and the pseudotime probe, which are a
check and a recorded probe (below). **Billed** is spawn's recorded window (stage-in, Docker, image pull
or env install, the command, stage-out) minus those two. $/hr is spawn's cost estimate over the
window. **Peak RSS**: *main* is the Python process's `ru_maxrss`, and *box* is the container cgroup's
`memory.peak`, which also counts the 16 loky workers.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| read + filter + normalise + HVG (with the count checks) | 34.1 | 49.7 | 29.6 |
| PCA + kNN + moments | 41.5 | 46.1 | 32.2 |
| steady-state + stochastic velocity | 18.2 | 29.1 | 13.1 |
| **`recover_dynamics`, 1,138 genes, 16 workers** | **449.4** | **865.9** | **522.0** |
| dynamical velocity + `velocity_graph` | 43.8 | 86.7 | 39.6 |
| *check: 16 genes refit with `n_jobs=1`* | *71.2* | *81.6* | *49.0* |

- **The dynamical model is the bill**: 77–82% of the workflow on every chip. Everything else on 89k
  cells, from reading the file to `velocity_graph`, takes 2–3.5 minutes.
- **AMD has the fastest cores, and Graviton4 is fastest on the full box.** On the serial refit of the
  same 16 genes, AMD is 1.45× Graviton4. With 16 loky workers it is 16% slower (522 s vs 449 s).
  From the refit's per-gene rate, Graviton4 runs the parallel fit at about 11× serial and AMD at
  about 7×. Why AMD scales worse was not established. The fits are paths that differ by chip, so the
  per-gene work is not identical either.
- **Intel loses on cores, not per core.** The `r8i` box has 8 physical cores with SMT, so 16 workers
  share 8 cores. Its serial refit is within 15% of Graviton4's, and its parallel fit is 1.9× slower.
  Two Intel runs took 809 s and 866 s for the same fit, a 7% spread. Graviton4's three runs agreed to 0.4%.
- **Graviton4 is cheapest per result, by 18% billed against AMD and 43% against Intel.** Its hourly
  price is the lowest and its full-box fit the fastest.
- **Overhead is 80–210 s.** Graviton4's arm64 image pull took 56 s to 2:00 across its runs. The x86 runs
  pull a small micromamba image and install the env twin offline in 6–7 s. These timings are not
  compute cost.
- **Memory picks the box.** The main process peaks at 38 GiB and the box at 61 GiB, with the 4.7 GB
  h5ad in tmpfs as well. A 64 GiB `m8g.4xlarge` would not fit. The 128 GiB box is the one run.
- n = 1 per chip for the table. The computation is deterministic within a chip: four Graviton4 runs
  (the sizing probe and three full runs) gave the same kNN graph, moments and velocity graph, and
  the three full runs the same likelihoods. Two Intel runs agreed the same way.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py); a failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| staged h5ad | sha256 `dd8e53a9…7b2a` (figshare's MD5 checked at fetch) | ok | ok | ok |
| spliced and unspliced are non-negative integers | the deposit | yes | yes | yes |
| **`initial_size_spliced/unspliced` == raw row sums; they add to the cell's molecules; the cells add to the dataset** | conservation | **exact**: 1,117,032,301 molecules | same | same |
| **`normalize_per_cell` multiplies every entry by one factor, median/initial size**, sparsity unchanged | its definition | max rel 1.42e-07 over 248.6 M + 43.0 M entries (float32) | same | same |
| **Ms, Mu == mean over each cell and its graph neighbours** | the definition | rel 5.0e-07 / 3.8e-07 | 5.3e-07 / 3.8e-07 | same as Xeon |
| **steady-state γ == the extreme-quantile least-squares slope, recomputed in float64**, within the per-gene float32 summation bound 2(n+1)u | the definition | 1.41e-05, **2.7% of bound** | 1.42e-05, 2.7% | same |
| **steady-state and stochastic velocity == Mu − γ·Ms** | the definition | **0** | **0** | **0** |
| **refitting 16 genes with `n_jobs=1` gives the 16-worker rates, likelihoods and times bit for bit** | each gene is fitted alone | identical | identical | identical |
| **dynamical residual velocity == β·Mu − γ·Ms from the fitted rates** | its definition | rel 8.4e-08 | 7.9e-08 | 8.2e-08 |
| every one of the 1,138 genes is recoverable; rates and likelihoods finite | — | yes | yes | yes |

Also held, from [`compare.py`](compare.py): **a gene's fit doesn't depend on which other genes are
fitted.** The 64-gene sizing probe and the 1,138-gene run were separate Graviton4 boxes. The 64 genes
they share have bit-identical α, β, γ, switch time, scaling and likelihood.

**Why the γ check rebuilds scVelo's float32 selection.** scVelo picks each gene's extreme-quantile
cells in float32 (`Ms/max + Mu/max ≥` its 95th percentile). Rebuilding that in float64 moves
borderline cells across the threshold for 1–2 genes, depending on the chip, and shifts γ by up to
4.2e-4. So the selection is rebuilt in scVelo's dtype, and the slope over it is recomputed
in float64. The bound is the worst case for scVelo's two float32 sums of n positive terms.

Reported, not asserted:
- 562 / 563 / 564 of 1,138 genes clear scVelo's 0.001 likelihood cut, and 499 / 498 / 500 pass as
  dynamical velocity genes. The median likelihood is 3–4e-05. Only 5.7% of molecules are unspliced
  in this atlas, which limits what the kinetic fit can resolve.
- The dynamical γ/β ranks the steady-state γ at Spearman 0.983.
- Velocity pseudotime (stochastic velocity) correlates with embryo stage at Spearman **0.624** on
  Graviton4 and Intel.

## Across chips: fixed point to the HVGs, path from the kNN graph

From [`compare.py`](compare.py) ([output](results/compare.txt)):

- **Fixed on all three chips:** every count and size identity above, to the printed digit. Also the
  10,766 filtered genes, the 2,000-HVG list (members and order), the 1,138 velocity genes and the 1,138
  stochastic velocity genes.
- **Path, from the kNN graph on:** the graph has 3,777,600 edges on Graviton4, 3,777,598 on Intel and
  3,777,590 on AMD. **Intel and AMD differ too**, on the same env twin and the same OpenBLAS kernel
  (`Cooperlake` on both, `neoversev2` on Graviton4). So the divergence isn't the BLAS kernel; it
  starts upstream of the moments. Those few edges move Ms, and everything downstream follows.
  Steady-state γ on the 1,138 velocity genes moves by up to 1.7% (Intel) or 3.3% (AMD). The dynamical rates are
  never bit-identical across chips (2 of 1,138 genes between Intel and AMD). Their median relative
  difference is 2e-4 to 2e-3 for α, β, γ and scaling, with Spearman ≥ 0.988, and 1–2% for the
  switch time (Spearman 0.90–0.93). Pseudotime agrees between Graviton4 and Intel at Spearman
  0.99999.
- **None of this is asserted across chips.** The fit is an optimisation started from moments that
  differ in the last bits. The paths are reported; the identities above hold on each chip.

## Two traps on this env, measured

**The default `scv.tl.velocity(adata)` raises on numpy 2.5.** The default mode is `stochastic`. Its
no-offset generalised least squares (`scvelo/tools/optimization.py:184`) assigns a shape-(1,) array
into a float32 array, and numpy 2.5 refuses (`ValueError: setting an array element with a sequence`).
This happened on all three chips. The run patches `leastsq_generalized` with the same arithmetic plus
`.item()`, then asserts the stochastic velocity identity above. This is the third workaround on this
env, after the recipe's two for `recover_dynamics`.

**`velocity_pseudotime` stalled on AMD.** It calls `terminal_states`, which runs ARPACK
`eigs(k=10, which="LR")` at the default `tol=0` on the 89k-cell transition matrix. That took 42 s on
Graviton4 and 35 s on Intel. On AMD's velocity graph it was still iterating in ARPACK after 20 minutes
(sampled with `py-spy`). The run bounds the probe at 300 s, and AMD records the timeout. It is a path:
AMD's graph differs from the others by about 1,000 of 24.15 M edges. Before trusting the defaults on
a large atlas, cap the work, for example by computing terminal states on a subset or passing
`root_key`/`end_key`.

## Pins

| | |
|---|---|
| data | figshare **article 14627496 v1**, doi:[10.6084/m9.figshare.14627496.v1](https://doi.org/10.6084/m9.figshare.14627496.v1) (CC BY 4.0, posted by scVelo's author), file 28095525 `gastrulation.h5ad`: 4,690,839,537 bytes, MD5 `aa364e67bb8d77fb0fcd0c1aa54ce20d` (figshare's), sha256 `dd8e53a97e99b8fb4cb3053d256b897cc56fd9596301d18e87bf7fb839107b2a`. Pijuan-Sala et al., *Nature* 2019, doi:10.1038/s41586-019-0933-9 |
| tier | durable public id (figshare DOI). Fetched on AWS by [`00-fetch.task.json`](00-fetch.task.json) and kept only on a size + MD5 match |
| scripts | `real.py`, `real.sh`, pinned with the data in [`pins.sha256`](pins.sha256) and checked in-task |
| image | the recipe's `single-cell@sha256:14d4af5b…` (scvelo 0.3.4, scanpy 1.12.4, anndata 0.13.4, pandas 3.0.6, numpy 2.5.4, scipy 1.18.1, OpenBLAS 0.3.34); x86 via `envs-x86_64/single-cell-957f8c9c38e0` |

**Why this dataset:** it is the largest dataset scVelo itself ships, the whole-atlas file rather
than the erythroid or E7.5 subsets it also ships. At 89k cells the dynamical model, not preprocessing,
decides the box and the bill. That doesn't show on the 3.7k-cell pancreas tutorial data.

## Caveats

- **The file is not the size scVelo's docstring says.** `scv.datasets.gastrulation` says 116,312
  cells. The deposited file has 89,267. It also carries a third layer keyed `None`, a byte-for-byte
  copy of `spliced` (same sum and nnz), and `X == spliced`.
- **Sizing came from a probe.** [`probe.task.json`](probe.task.json) ran the whole workflow with the
  dynamical fit capped at 64 randomly sampled velocity genes ([results/probe](results/probe/)). The
  table is from the full runs.
- **Absolute rates are not on a common clock**, as in the recipe: `t_max=False` skips alignment.
  γ/β is unaffected.
- **Spend:** $2.96 in total for the fetch, four probes, and eight full or partial runs. The final three
  runs cost $0.238 (Graviton4), $0.404 (Intel) and $0.378 (AMD), billed. The first Intel run failed
  the γ check while it still used a float64 selection. The first AMD run was stopped in the
  pseudotime solve. Both fixes changed `real.py`, so all three chips were re-run.
