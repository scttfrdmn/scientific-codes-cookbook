# geo-ml — spatial join, CRS-aware area, PySAL weights, and a two-engine OLS

One task. The geospatial-ML stack (geopandas, libpysal, scikit-learn, statsmodels)
computes a spatial join, a projected area, a spatial-weights graph, and a regression, and
the smoke check confirms six exact-or-wrong identities.

> **What this recipe does and does not cover.** It exercises `geo-ml`'s analysis layer on
> small constructed inputs — enough to prove geopandas/PySAL/sklearn/statsmodels work
> correctly on Graviton4. Not a benchmark; no large dataset, and it doesn't touch the
> heavier learners (xgboost/lightgbm ship in the env but aren't exercised here).

## Why one task, and why nothing is staged

One environment, one `python3` invocation. Every input is constructed in code — a set of
points and polygons, a lattice, a line of collinear data — so there is **no input to
stage** and no `stage-inputs.sh`; the image digest is the only pin. This is deliberately
the *synthetic-and-cheap* shape (see `recipes/geospatial`): constructing the inputs is
what lets every check be exact rather than banded. `geo-ml` is the second geo/EO recipe
and confirms that shape generalizes across the domain's analysis envs.

## Six identities, no thresholds

| observable | assertion | observed |
|---|---|---|
| **OLS R²** | exactly 1.0 (fit to collinear y = 3x + 7) | 1.000000000000 |
| **OLS slope / intercept** | 3.0 / 7.0 | 3.0 / 7.0 |
| **sklearn == statsmodels** | slope, intercept, R² match to ≤ 1e-9 | match |
| **spatial join count** | exactly 3 points within the two squares (geopandas) | 3 |
| **projected area** | exactly 1,000,000 m² (1 km square in EPSG:32611) | 1000000.000000 |
| **PySAL rook links** | n = 9, s0 = 24 (3×3 lattice, rook contiguity) | n=9, s0=24 |

Each earns its place:
- **Collinear OLS** — fitting a line to exactly collinear points has a closed-form answer:
  R² = 1, slope 3, intercept 7. Exact, no tolerance.
- **Two-engine cross-check** — scikit-learn (normal-equations/SVD) and statsmodels agree
  to machine precision on the same fit. Two independent implementations, one answer — the
  cross-validation move, applied to regression.
- **Spatial join count** — five points against two disjoint unit squares, `within`
  predicate: exactly three fall inside. A wrong spatial index or predicate lands elsewhere.
- **Projected area** — a 1 km × 1 km square in a metric CRS (UTM 11N) has area exactly
  1e6 m². This is the CRS-aware geometry path (GEOS + the projection), and it's exact.
- **PySAL rook contiguity** — a 3×3 lattice has 12 shared edges → 24 directed neighbor
  links (`s0`). An exact property of the spatial graph.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/geo-ml@sha256:ed8c59dedeb4c1bc9d4cfef85f14740947f9b1ed23bb00d083c6f163b25529e0` |
| | tag `2026.09.04`, geopandas + libpysal + scikit-learn + statsmodels + xgboost + lightgbm, cosign-signed, `linux/arm64` |
| input | constructed geometries / data, **in-task** — nothing staged |

**Data tier: synthetic / in-task.** The image digest is the only pin.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The work is
**~1 second**, single-threaded.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.80 GB** `geo-ml` image are the whole task. The recorded run's command window was
**87s** (00:17:49 → 00:19:16 UTC), all six identities exact. TTL was **retightened from
that first real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger
blast radius, not caution; the recorded run used the original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — everything is constructed in code.

```sh
spawn task run --spec recipes/geo-ml/01-spatial.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/geo-ml/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect
three objects (`geoml-results.txt`, `geoml-results.json`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
no instance was created, so re-run.
