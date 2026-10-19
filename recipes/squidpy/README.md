---
tool: squidpy
tool_version: "1.8.2"
env: single-cell
image: quay.io/aarchsci/single-cell@sha256:14d4af5bbd86625e3c98eb7b2078e3da6206aa9cb1ea3a3d631e96c7bebb901a
env_x86_64: single-cell-957f8c9c38e0
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# Squidpy — spatial graphs, neighbourhood enrichment and Moran's I, each against a known answer

Builds kNN, radius and Visium-style hex graphs, scores neighbourhood enrichment and spatial autocorrelation on Graviton4, and checks each against brute force, a closed form or a planted tissue layout. For anyone doing spatial omics on ARM.

## Run it

```bash
make stage RECIPE=squidpy     # once: the checks only — every cell, label and gene is synthesised
spawn task run --spec "$(make -s spec RECIPE=squidpy)" --wait
make ls RECIPE=squidpy

sq.gr.spatial_neighbors_knn(adata, n_neighs=6)                       # == brute-force kNN, exactly
sq.gr.nhood_enrichment(adata, cluster_key="ct", connectivity_key="rad", seed=0)
sq.gr.spatial_autocorr(adata, mode="moran")                          # == closed form to 2e-15
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `spatial_neighbors_knn` / `_radius` / `_grid` | `spatial_neighbors(coord_type=...)` | the one-call form is **deprecated in 1.7 and removed in 1.9**; the mode-specific functions are the ones that keep working. |
| `spatial_neighbors_grid(n_neighs=6, n_rings=k)` | your Visium slide | interior spots get exactly **3k(k+1)** neighbours (6, 18, 36); edge spots fewer, so ring count changes who counts as "neighbour". |
| `nhood_enrichment(..., seed=0)` | no seed | the z-score is against a permutation null, so without a seed it moves run to run. It is in units of that null's sd, which is what makes 5 a 5-sigma call. |
| `spatial_autocorr(mode="geary")` | — | **Geary's analytic variance was wrong before 1.8.2** (Moran's was reused). Here it matches Cliff & Ord; an older squidpy gives different `pval_norm`. |
| 2,000 uniform cells, two-domain layout | your `obsm["spatial"]` and labels | nothing is fetched; positions are continuous so the kNN graph has one right answer. |

**Leave the fixture.** Every check is an identity or a planted layout, so 2,000 cells is enough to make the planted signal 36–68 sigma and the brute-force graph cheap. **Scale it** to a real slide: the graph and closed-form identities hold at any size; only the brute-force reference becomes O(n²).

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB; 0.7 GB peak RSS measured), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The checks take seconds of the window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: graphs equal brute force exactly, enrichment counts equal M'AM, Moran's I equals its closed form to 2e-15, and a planted layout recovered at 36-55 sigma</summary>

### The checks

| observable | assertion | observed (Graviton4) | x86_64 (`c8i`) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | 1 of 1 |
| squidpy / scanpy / anndata / numpy | read from the running install | **1.8.2** / 1.12.4 / 0.13.4 / 2.5.4 | identical |
| **kNN graph (k=6) vs brute force** | **edge set equal; distances `< 1e-12`** | **0** differing entries of 12,000; **0.000e+00** | identical |
| **radius graph (r=4) vs brute force** | **edge set equal, symmetric** | **0** differing of 19,318; **0** asymmetric | identical |
| **hex grid interior degree, k = 1, 2, 3 rings** | **exactly 3k(k+1), no self-loops** | **6 / 18 / 36** on every interior spot | identical |
| **enrichment counts vs M'AM** | **equal as integers, sum = edges** | **equal** | identical |
| **planted A–B / A–C / B–C z** | **> +5 / < −5 / < −5** | **+36.10 / −54.74 / −55.30** | identical |
| control: same cells, labels shuffled | every \|z\| `< 5` | max 2.94 | identical |
| **Moran's I / Geary's C vs closed form** | **`< 1e-10`** | **2.109e-15 / 1.332e-15** | **2.331e-15 / 1.998e-15** |
| **analytic var, Moran / Geary vs Cliff & Ord** | **rel `< 1e-5`; Geary ≠ Moran** | **2.9e-07 / 1.1e-06**; 15% apart | identical |
| **I(y) vs I(−y)** | **`== 0` exactly** | **0.000e+00** | identical |
| **I(y) vs I(3.7y + 112.5)** | **`< 1e-12`** | **1.887e-15** | identical |
| planted spatial gene / random gene | z `> 5` / \|z\| `< 5` | **+67.8** / −0.99 | identical |

**x86_64** runs the same spec on the env's linux-64 twin (`envs-x86_64/single-cell-957f8c9c38e0`,
535 packages installed offline in 58 s on `c8i.large`; the nine version differences, listed on
[scvelo](../scvelo/README.md), include none of squidpy, scanpy, anndata, numba or numpy). Every
graph, every integer count, every permutation z-score and both invariances are identical across
chips, the seeded permutations included. Only the closed-form residuals move, by a few ulps: they
are float64 summation order, and sit five orders inside the bound on both.

### Why these bounds

A graph is a set of edges, so the claim is equality with brute force; positions are continuous
uniform so no two distances tie and "the six nearest" is unique. Enrichment's count matrix is
`M'AM` for the one-hot label matrix `M` — an integer identity. Its z-score is in units of the
permutation null's own standard deviation, so ±5 is 5 sigma by construction, not a margin over
this run. The planted layout puts A and B mixed on the left half and C alone on the right, so
A–B must be enriched and A–C, B–C depleted; the shuffled control is the same cells and graph.

Moran's I and Geary's C are recomputed in numpy from the same row-normalised weights; two float64
routes agree to round-off, bounded at `1e-10`. The analytic variances carry float32 round-off,
because squidpy forms S0–S2 from float32 connectivities — hence rel `1e-5`. The two invariances
are not the same strength: negation is exact in IEEE arithmetic, so I(−y) is asserted
**bit-identical**, while adding 112.5 rounds each element once (~2.5e-14 on unit-scale data), so
the affine one carries a `1e-12` bound.

### Pins

| | |
|---|---|
| data | **none staged** — 2,000 uniform positions in a 100×100 field, a two-domain A/B/C layout, a `sin(2πx/50)` gene plus noise and a pure-noise gene, a 30×30 hex lattice; all from `default_rng(20261016)` |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/single-cell@sha256:14d4af5bbd86…` — squidpy 1.8.2, scanpy 1.12.4, scvelo 0.3.4, numba 0.68.0, numpy 2.5.4 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`c422f5f8c974…`), so verify that and pin the arm64 entry. A newer digest than
[seurat](../seurat/README.md) pins — that recipe stays on the image it was verified against.

### Run + verify

```sh
make stage RECIPE=squidpy
spawn task run --spec "$(make -s spec RECIPE=squidpy)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/squidpy/r1/score.tsv" -
```

Fails on a pin mismatch, a graph that is not the brute-force graph, a hex degree that is not
3k(k+1), enrichment counts that are not `M'AM`, a planted layout not recovered at 5 sigma or a
shuffled one that is, a Moran/Geary statistic or variance off its closed form, an invariance that
does not hold — or a NaN anywhere a bound is applied. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real slides**: nothing reads Visium, Xenium or MERFISH output, so SpatialData I/O, image
features (`sq.im`) and segmentation are untouched. **Ligand–receptor analysis** (`sq.gr.ligrec`),
co-occurrence, Ripley's statistics and centrality scores. **Delaunay graphs**, which squidpy builds
with scipy and so have no reference independent of it here. **Plotting.**

</details>
