---
tool: geopandas
env: geo-ml
image: quay.io/aarchsci/geo-ml@sha256:ed8c59dedeb4c1bc9d4cfef85f14740947f9b1ed23bb00d083c6f163b25529e0
spawn_version: 0.104.0
---
# geopandas + PySAL + sklearn (geo-ml env) — spatial join, projected area, weights, two-engine OLS

The geospatial-ML stack (geopandas / libpysal / scikit-learn / statsmodels) computes a spatial join, a CRS-aware area, a spatial-weights graph, and an OLS fit — the core operations of spatial data science.

> **What this covers.** The `geo-ml` analysis layer (geopandas / libpysal / scikit-learn / statsmodels) on small constructed inputs — proof it's correct on Graviton4. Not a benchmark; the heavier learners (xgboost/lightgbm ship in the env) aren't exercised here.

## Run it

```python
import geopandas as gpd
from shapely.geometry import box
from libpysal.weights import lat2W
# points, squares, a 3×3 lattice are built in code (the recipe constructs them); the core ops:
gpd.sjoin(points, squares, predicate="within")                  # → 3 points inside
gpd.GeoSeries([box(0, 0, 1000, 1000)], crs="EPSG:32611").area   # → 1,000,000 m² (1 km square)
lat2W(3, 3, rook=True)                                          # rook contiguity → n=9, s0=24
```

One task, one `python3` invocation. Every input is constructed in code, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the constructed points / polygons / 3×3 lattice / collinear line | your own GeoDataFrames | constructing the inputs is what makes every check *exact* rather than banded — the small synthetic input is the point, not a compromise. |
| the OLS on `y = 3x + 7` | your model + real data | fit to exactly collinear points, the answer is closed-form (R²=1, slope 3, intercept 7); real data has no such identity to assert. |
| EPSG:32611 (UTM 11N) for the area | your metric CRS | the 1 km² area identity is CRS-aware — a geographic CRS would give degrees², not m². |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** every identity is exact at this size and hand-checkable; a large dataset is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The work is ~1 s, single-threaded. Recorded command window **87s** — boot, Docker install, and the ~0.80 GB `geo-ml` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: six identities, the two-engine cross-check, pins, run + verify</summary>

### Six identities, no thresholds

| observable | assertion | observed |
|---|---|---|
| **OLS R²** | exactly 1.0 (fit to collinear y = 3x + 7) | 1.000000000000 |
| **OLS slope / intercept** | 3.0 / 7.0 | 3.0 / 7.0 |
| **sklearn == statsmodels** | slope, intercept, R² match to ≤ 1e-9 | match |
| **spatial join count** | exactly 3 points within the two squares (geopandas) | 3 |
| **projected area** | exactly 1,000,000 m² (1 km square in EPSG:32611) | 1000000.000000 |
| **PySAL rook links** | n = 9, s0 = 24 (3×3 lattice, rook contiguity) | n=9, s0=24 |

The **sklearn == statsmodels** row is a [two-engine cross-check](../../practices/cross-checks.md): two independent OLS implementations (normal-equations/SVD vs statsmodels) agreeing to machine precision on the same fit is far stronger than either alone — the cross-validation move applied to regression. The rest are closed-form or exact graph/geometry properties: a 3×3 lattice has 12 shared edges → 24 directed neighbor links; a 1 km square in a metric CRS has area exactly 1e6 m² (the GEOS + projection path); the `within` predicate puts exactly 3 of 5 points inside.

### Pins (data tier: synthetic / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/geo-ml@sha256:ed8c59dedeb4c1bc9d4cfef85f14740947f9b1ed23bb00d083c6f163b25529e0` (tag `2026.09.04`, geopandas + libpysal + scikit-learn + statsmodels + xgboost + lightgbm, cosign-signed, `linux/arm64`) |
| input | constructed geometries / data, **in-task** — nothing staged |

### Run + verify

```sh
make run RECIPE=geo-ml
make ls RECIPE=geo-ml
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect three objects (`geoml-results.txt`, `geoml-results.json`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
