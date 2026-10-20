# Squidpy on two whole spatial sections — Graviton4, Intel and AMD

> **A whole Slide-seqV2 puck and a whole Visium capture area go through graphs, neighbourhood
> enrichment, co-occurrence and genome-wide Moran's I / Geary's C, and every identity holds on all
> three chips.** Every graph, every enrichment count and every seeded z-score is identical across
> chips. AMD is 1.32× Graviton4 and cheapest billed; Graviton4 is cheapest in compute. The run found
> that squidpy's permutation functions leave numba on one thread for the rest of the session, which
> made co-occurrence 5.2× slower.

Workload: two whole sections from the CELLxGENE Census LTS release, each the unit a squidpy analysis
runs on:

- **Slide-seqV2**, healthy human kidney medulla, `Puck_200903_13` (Marshall et al., *iScience* 2022):
  **44,588 beads × 27,007 genes**, 19,428 of them detected in ≥ 10 beads, with 15 cell types from the
  authors' annotation.
- **10x Visium**, human myocardial infarction, `RZ_BZ_P12` (Kuppe et al., *Nature* 2022): **3,420
  in-tissue spots × 36,406 genes**, 14,828 of them detected in ≥ 10 spots, with 9 spot labels.

On each section, from the raw counts through `normalize_total` and `log1p`, the run executes:

- the spatial graph: kNN (k = 6) and radius (r = 30 puck units) on the puck, and the hex grid at
  1, 2 and 3 rings on the slide;
- `nhood_enrichment` on the authors' cell types, 1,000 permutations;
- `co_occurrence` at 41 radii (puck only);
- `spatial_autocorr` with Moran's I and Geary's C on every tested gene;
- Moran's I with a 100-permutation test on the 2,000 HVGs, the way squidpy's own tutorials run it.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/squidpy-real/stage-inputs.sh "$B"     # 289 MB, Census -> bucket, server-side
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py squidpy $a "$B" "$(date +%s)" \
    measurements/squidpy-real/real.task.json)" --wait
done
PYTHON=<python with numpy+pandas> bash measurements/squidpy-real/collect.sh "$B" <scratch dir>
```

[`real.py`](real.py) is the workload and its checks; [`real.sh`](real.sh) runs it on both sections
twice, in fresh processes (r1, r2), and requires every output except the timings to be
byte-identical. The calls a user writes are:

```python
sq.gr.spatial_neighbors_knn(adata, n_neighs=6)                     # puck; _grid(n_rings=k) on the slide
sq.gr.nhood_enrichment(adata, cluster_key="cell_type", seed=0)     # 1000 perms
sq.gr.co_occurrence(adata, cluster_key="cell_type", interval=radii)
sq.gr.spatial_autocorr(adata, mode="moran")                        # and mode="geary", every gene
sq.gr.spatial_autocorr(adata, genes=hvg, mode="moran", n_perms=100, seed=0, n_jobs=16)
numba.set_num_threads(16)    # after each permutation call: squidpy leaves numba on 1 thread (below)
```

## Results

16 vCPU, `4xlarge`, 32 GiB per chip. The arm64 run uses the recipe's image digest; the x86 runs use
the `single-cell` env's linux-64 twin (squidpy, scanpy, anndata, numba and numpy at the same
versions). Same staged bytes, same seed, same `n_jobs`:

| chip | instance | tool wall (one result) | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 712 s | 816 s | 9.4 GiB | 0.6381 | **0.1262** | 0.1446 |
| Xeon 6975P-C | `c8i.4xlarge` | 744 s | 806 s | 9.5 GiB | 0.7497 | 0.1549 | 0.1678 |
| **EPYC 9R45** | `c8a.4xlarge` | **539 s** | **586 s** | 9.5 GiB | 0.8622 | 0.1291 | **0.1403** |

A result is one verified pass over both sections: every squidpy call, plus every check against its
reference. **Tool wall** is r1 (both sections, from a cold numba cache). **Billed** is spawn's
recorded window (stage-in, Docker, image pull or env install, the command) minus the r2 replicate,
i.e. what one pass bills. $/hr is spawn's cost estimate over that window. **Peak RSS** is the main
process's `ru_maxrss`; the container peaked at 14.8 / 20.6 / 20.6 GiB, counting the 16 permutation
workers and, on x86, the env install.

The squidpy steps themselves, on the puck (r1, seconds):

| step | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| kNN + radius graphs | 0.34 | 0.32 | 0.25 |
| `nhood_enrichment`, 1000 perms | 3.40 | 2.76 | 2.24 |
| `co_occurrence`, 41 radii, 2.0e9 pairs | 6.57 | 7.80 | 4.81 |
| Moran's I, 19,428 genes | 83.3 | 65.7 | 62.5 |
| Geary's C, 19,428 genes | 75.8 | 66.3 | 62.0 |
| Moran's I, 2,000 HVGs × 100 perms, 16 workers | 134.5 | 125.8 | 76.3 |
| **squidpy subtotal** | **303.9** | **268.6** | **208.0** |

The slide's whole squidpy analysis takes 25.7 / 25.1 / 18.7 s.

- **On squidpy's own steps AMD is 1.46× Graviton4 and Intel 1.13×.** Graviton4 is slowest at the
  numba statistics. It wins back the whole-run order on the checks, which are numpy (exhaustive
  distance blocks and dense reductions): brute-force kNN 43.9 s against Intel's 50.2 s, the closed
  forms 28.5 s against 54.5 s. Priced on the squidpy subtotal alone, AMD is $0.050 per pass,
  Graviton4 $0.054, Intel $0.056.
- **The permutation test is the bill.** 100 permutations on 2,000 genes cost more than the
  analytic statistic on all 19,428. With `n_jobs=16` that step is the only one that uses the box.
  Moran's I in scanpy's numba kernel gains only 1.4–2.0× from 16 threads; the kernel allocates
  per row.
- **Overhead is small.** 47–104 s of stage-in, boot and image pull or env install against a
  9–12 minute pass. These timings are still not compute cost: a single pass is short, and a
  second section on the same box would not pay the overhead again.
- **Memory does not pick the box.** 9.5 GiB peak for the main process; the inputs are 289 MB. A
  `c8g.2xlarge` (16 GiB) would probably hold one pass with fewer workers, but that size was not run.
- n = 1 per chip, and r1/r2 are byte-identical on each chip (all 18 outputs), so each timing is of
  one fixed computation. r2's walls are within 3% of r1's.

## Two traps squidpy sets, measured

**Every in-process permutation call turns numba down to one thread for the rest of the session.**
squidpy's runner calls `numba.set_num_threads(1)` before each chunk
(`squidpy/_utils.py:77`, `_callback_wrapper`). Under the default `n_jobs=1`, or the threading backend,
that chunk runs in the calling thread, so the setting outlives the call. The run records
`numba.get_num_threads()` after each call: 1 after every `n_jobs=1` `nhood_enrichment`, on all three
chips. Everything numba-parallel that runs afterwards is serial, in squidpy (`co_occurrence`, Moran,
Geary) and in scanpy. An earlier Graviton4 run without the reset took **34.5 s for `co_occurrence`
against 6.6 s** with it, and 165 s against 83 s for Moran's I on all 19,428 genes. The run measures the
Moran step both ways on 2,000 genes: 16.7 / 8.4 s on Graviton4, 10.4 / 6.9 s on Intel, 9.3 / 6.4 s on
AMD. Any session that runs enrichment before co-occurrence or autocorrelation hits it. The fix is
`numba.set_num_threads(n)` after each permutation call.

**`nhood_enrichment(n_jobs > 1)` raises on any graph over 262,144 edges.** The counting kernel is
compiled with an explicit signature for writable `uint32` arrays. The loky backend memory-maps any
argument over 1 MB read-only, and the graph's `indices` cross 1 MB at 262,144 edges. The puck's
267,528-edge kNN graph raises `TypeError: No matching definition for argument type(s) readonly
array(uint32, 1d, C), …` on all three chips; the slide's 19,946-edge grid runs. So
`n_jobs=1` is the only way to run enrichment on a puck-sized section, and that is what sets the trap
above.

**And `seed` alone does not pin a permutation test.** squidpy seeds each worker's chunk from its
position (`seed + first index`), so the same seed under a different `n_jobs` draws a different null.
Enrichment z-scores moved by up to 8.1 (puck) and 0.9 (slide) between `n_jobs=1` and `n_jobs=16`. Pin
`n_jobs` with the seed.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py); a failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| counts are non-negative integers; obs names unique; positions finite | the deposit | yes | yes | yes |
| **kNN graph == exhaustive float64 kNN, every bead** | a set of edges | **0 of 44,588 differ**; distances rel 0 (10 beads tie at k = 6, checked by distance) | same | same |
| **radius graph == exhaustive, symmetric** | a set of edges | **0 of 268,136 differ** | same | same |
| **grid graph from pixel coordinates == the hex lattice from `array_row`/`array_col`, at 1, 2, 3 rings** | an independent coordinate system | **0 of 19,946 / 58,898 / 115,898 edges differ**; ring labels == hop counts | same | same |
| grid: every spot with a complete ring has exactly 3k(k+1) neighbours | hex geometry | 6 / 18 / 36 on 3,135 / 2,860 / 2,603 spots | same | same |
| **enrichment counts == M′AM (one-hot labels), sum == edges** | an integer identity | equal, 5 calls per section | same | same |
| planted halves on the real positions: left–left, right–right z > 5, left–right z < −5 | planted truth | +326 / +338 / −326 (puck) | same | same |
| shuffled labels: \|z\| < 5 on pairs with ≥ 50 expected edges | the null is the permutation itself | 2.22 (128 pairs) | same | same |
| **co-occurrence == KD-tree pair counts at 39 radii** | integer counts, one right answer | 2.8e-14 | same | same |
| co-occurrence past the section's diameter == n/(n−1), n(nᵢ−1)/((n−1)nᵢ) | closed form | 2.2e-16 | same | same |
| **Moran's I == closed form, every gene** (≤ per-gene float64 summation bound) | the definition | **4.9e-15**, 0.4% of bound | **0**, 0% | **0** |
| **Geary's C == edge-by-edge closed form, every gene** (≤ per-gene bound) | the definition | 2.4e-12, 5.4% of bound | same | same |
| analytic variances vs Cliff & Ord; Geary's ≠ Moran's | squidpy 1.8.2's Geary fix | rel 1.9e-8 / 7.0e-8 | same | same |
| **I(−y) == I(y), every gene, bit for bit** | IEEE negation is exact | **0** on 19,428 + 14,828 | same | same |
| **I(3.7y + 112.5) vs I(y), every gene** (≤ per-gene rounding bound) | invariance | 5.8e-13, 23% of bound | same | same |
| a gene's I is the same whichever genes are tested with it | per-gene computation | 0 | 0 | 0 |
| permutation p-values in [1/101, 1] | definition | yes | yes | yes |
| planted wave / pure noise on the real positions: z > 5 / \|z\| < 5 | planted truth | +126.0 / −0.01 (puck) | same | same |

"Same" means the value printed identically. The Visium column values are in
[`results/`](results/) and hold the same way. The bounds are derived, not chosen:

- **Summation.** Each statistic is a few float64 sums over n terms, so two evaluations can differ by
  the recursive-summation bound n·u (u = 2⁻⁵³) on each, scaled by the gene's own `Σ|z∘Wz| / z′z`
  (Moran) or `Σy² / z′z` (Geary). scanpy expands Geary's denominator, which cancels when the mean
  is large against the spread.
- **Affine shift.** Forming 3.7y + 112.5 rounds each element by at most 2u(112.5 + 3.7 max|y|). Its
  first-order effect on I is computed per gene.
- **Weights.** The closed forms use squidpy's own row-normalised float32 weights, widened to float64.
  The weights' float32 rounding is an input to the statistic, not an error in it.
- **Co-occurrence.** The check rounds positions to 1/8 unit (≤ 1/16 unit moved). That makes every
  pair distance under 256 units exact in squidpy's float32, and each threshold is kept off that
  lattice, so the count at each radius has exactly one right answer.

Reported, not asserted: on the puck, 7,486 of 19,428 genes are spatially variable at BH-FDR < 0.05
(analytic), and 670 of the 2,000 HVGs by permutation. The top Moran's I genes include the
segment markers UMOD (thick ascending limb, 0.410) and AQP2 (collecting duct, 0.368), alongside
immunoglobulin, haemoglobin and mitochondrial transcripts. Every cell type with more than 9
beads is self-enriched (z 12.5–168). On the slide, 849 of 14,828 genes pass analytically and none of
the HVGs by permutation: with 100 permutations the smallest p is 1/101, which BH cannot carry below
0.05 across 2,000 tests unless hundreds sit at that floor.

## Across chips: fixed points, and one tie-break

From [`compare.py`](compare.py) ([output](results/compare.txt)):

**Identical on all three chips:**
- every graph's edge list (kNN, radius, grid at 1–3 rings);
- every enrichment count matrix, and every seeded enrichment z-score: 1,000 permutations from the
  same seed draw the same null on every chip;
- the gene ranking by Moran's I and by Geary's C on both sections.

**Intel and AMD are byte-identical to each other** in every output. They run the same linux-64 twin;
OpenBLAS is not on the path of anything compared here (graphs come from sklearn's KD-tree, the
statistics from numba).

**Graviton4 against x86, the statistics differ in the last bit:**
- Moran's I is bit-identical on 19,246 of 19,428 puck genes (max |Δ| 3.0e-16) and 14,277 of 14,828
  slide genes (1.9e-15).
- Geary's C is bit-identical on 18,169 of 19,428 (8.9e-16) and 11,787 of 14,828 (1.6e-14).
- Co-occurrence differs by at most 2.2e-16. Its counts are identical; it is a ratio of them.

The x86 numba build gives a closed-form Moran difference of exactly 0, and the arm64 one 4.9e-15. Both
are deep inside the bound.

**The permutation p-value is a tie count, and the tie is decided by those last bits.** 14 of the
2,000 puck HVGs (16 on the slide) get a different `pval_sim` on Graviton4 than on x86. The worst moves
by 21 of 100 permutations, though on those genes `var_sim` agrees to 6e-16 and the permutations are
the same. These
are sparse genes, expressed in 13–100 of 44,588 beads. Their permuted Moran's I falls on a near-lattice
(as few as 15 distinct values in 100), and between 11 and 96 of the 100 permuted values lie within
1e-14 of the observed I ([`ties.py`](ties.py), [output](results/ties-arm64.txt), one Graviton4 run).
Whether `sims >= score` counts them is a last-bit question. It is a path, so it is reported, not
asserted. Two dense controls (632 and 3,779 beads) have no ties.

## Pins

| | |
|---|---|
| data | CELLxGENE Census **LTS 2025-01-30** (`do_not_delete`), `h5ads/88b7da92-178d-4f27-bc18-b0165b256c67.h5ad` (90,926,645 bytes, ETag `0968e4bf…-11`, sha256 `03c0a267…e9aa`) and `h5ads/05d4a216-5384-4724-bd2a-2ea38f02e7cc.h5ad` (197,717,507 bytes, ETag `7ecd352f…-24`, sha256 `199a2c5f…dac4`); full digests in [`inputs.sha256`](inputs.sha256) |
| tier | RODA (`s3://cellxgene-census-public-us-west-2`), copied server-side; S3 computes each copy's SHA256 |
| scripts | `real.py`, `real.sh`, pinned with the data in [`pins.sha256`](pins.sha256) and checked in-task |
| image | the recipe's `single-cell@sha256:14d4af5b…` (squidpy 1.8.2, scanpy 1.12.4, anndata 0.13.4, numba 0.68.0, TBB threading layer on arm64, numpy 2.5.4); x86 via `envs-x86_64/single-cell-957f8c9c38e0` |

**Why these sections:** a squidpy analysis runs per section, so the honest unit is one whole
section, not a cohort. The two cover both kinds of spatial graph, continuous positions (Slide-seq)
and an array lattice (Visium). The puck is near-single-cell at ~45k observations. That is where the
graph, enrichment and co-occurrence costs and squidpy's own scaling limits appear (the 262,144-edge
`n_jobs` ceiling, the O(n²) co-occurrence loop). Both carry the authors' annotations, and both are on
RODA with a durable release.

## Caveats

- No Xenium or MERFISH run. A 100k–1M-cell imaging section would put `co_occurrence`, which loops
  over all n² pairs, at 5–500× the puck's cost. Not measured.
- `ligrec` (needs OmniPath at run time), Ripley's statistics, `sq.im` image features and
  segmentation were not run.
- The x86 `host.txt` has no OpenBLAS corename: the probe asks threadpoolctl before numpy loads.
  OpenBLAS 0.3.34 picks `neoversev2` on Graviton4 and `Cooperlake` on both x86 chips
  ([scanpy-real](../scanpy-real/README.md), [nilearn-real](../nilearn-real/README.md)). It does not
  enter the checked computations here.
- `cost_limit` is 1.2 (×1.3 / ×1.6 for x86 via `spec_arch.py`), TTL 2 h as a backstop; the runs
  took 19–26 min each.

## Spend

About **$1.42** of EC2:
- the three verifying runs, $0.855;
- a structure probe, $0.12;
- a run stopped when it showed squidpy's thread trap, ~$0.20;
- a run on the pre-final bounds, $0.24;
- the tie diagnostic, $0.005.

The staged inputs are 289 MB in the bucket.
