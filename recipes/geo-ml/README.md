---
tool: geopandas
tool_version: 1.1.4
env: geo-ml
image: quay.io/aarchsci/geo-ml@sha256:ed8c59dedeb4c1bc9d4cfef85f14740947f9b1ed23bb00d083c6f163b25529e0
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# geopandas + libpysal — recover Census's published county areas, then do spatial statistics

Reads 3,235 US counties (8.2M vertices), recomputes the areas Census published alongside them, and builds contiguity weights and Moran's I. For spatial analysis on ARM.

## Run it

```bash
make stage RECIPE=geo-ml                                           # once: 84 MB, pinned
spawn task run --spec "$(make -s spec RECIPE=geo-ml)" --wait       # ~22 s of compute
```

```python
g = gpd.read_file("tiger.zip")                    # 3235 counties, EPSG:4269
geod = Geod(ellps="WGS84")
abs(geod.geometry_area_perimeter(g.geometry[0])[0])   # matches that county's ALAND+AWATER
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| TIGER2024 counties | tracts, block groups, another year | TIGER is versioned by year on a stable path; every level carries `ALAND`/`AWATER`, so the reference comes along. |
| `Geod(ellps="WGS84")` | a projected CRS | **this is the load-bearing choice** — see the projection table below before you reach for `.area`. |
| `log10(ALAND)` for Moran's I | your own variable | the invariance assertion holds for any variable; the *value* 0.605 does not. |
| rook contiguity | `Queen`, KNN, distance bands | rook is the one with an exact shared-edge count to check against. |

**Leave the fixture.** 3,235 polygons is already a real workload — 8,235,114 vertices and 14.7 s of
contiguity building — and it is the largest unit at which Census publishes an area for every
feature, which is what makes the check possible. Tracts would be more rows, not a stronger claim.

## Which box

`m8g.large` (2 vCPU / 8 GiB). Measured: read 0.8 s, geodesic area over 8.2M vertices **6.9 s**,
rook contiguity **14.7 s** — 22 s of compute, all single-threaded, so RAM picks the box and not
cores. Docker install and the image pull dominate the window; **these timings are not compute
cost** ([layout](../../patterns/layout-and-effective-cost.md)).

`m8g` over `c8g` because the geometry and the weights matrix are the constraint: 84 MB of staged
zip expands to 8.2M coordinate pairs plus a 17,762-link adjacency, and nothing here scales with
vCPU count.

<details>
<summary>As shipped: a reference that ships inside the data, three exact invariances, one trap</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| features | exactly 3235 (TIGER2024 county file) | 3235 |
| CRS | EPSG:4269, as TIGER ships | 4269 |
| **geodesic area vs Census** | **max rel err < 1e-5 over 3235 counties** | **6.710e-07** (median 5.2e-08) |
| total area conserved | sum rel err < 1e-6 | **6.372e-08** |
| CONUS subset | exactly 3109 | 3109 |
| weights symmetric | every link reciprocated | yes |
| links even | `s0` = 2 × shared edges | 17762 = 2 × 8881 |
| no islands | 0 after dropping island states | 0 |
| row-standardised | `s0` exactly = `n` | 3109.000000 |
| **Moran's I affine-invariant** | **I(y) == I(3.7y + 112.5) exactly** | **\|diff\| = 0.00e+00** |
| **Moran's I sign-invariant** | **I(y) == I(−y) exactly** | **\|diff\| = 0.00e+00** |
| Moran's expectation | `EI` = −1/(n−1) | −0.000321750322 |
| **three OLS routes agree** | **max pairwise diff < 1e-12** | **8.88e-15** |

**The reference ships inside the data.** TIGER's `.dbf` carries `ALAND` and `AWATER` — Census's own
computed land and water areas in m² — beside each polygon. Recovering them from the geometry makes
this a reproduction rather than a measurement, and it is the best kind of reference because the
reference and the data are *the same pinned object*: no version to match, nothing to drift
([the practice](../../practices/reference-from-tests.md)). The residual is Census's own precision —
integer m² on counties of order 1e9 m² — not a band fitted to the result.

**It only works in the right space, and the wrong space fails quietly-ish.** The same areas through
two projected CRSs, as observations rather than assertions (Mercator's distortion is a mathematical
certainty, not a property of this build):

| method | median rel err | max rel err |
|---|---|---|
| **geodesic, WGS84 ellipsoid** | **5.2e-08** | **6.7e-07** |
| Albers equal-area (EPSG:5070) | 5.4e-08 | **4.5e-05** |
| Web Mercator (EPSG:3857) | **0.62** | **7.02** |

Albers is the instructive one. It is equal-area and its median is as good as the geodesic answer —
but it is parameterised for the lower 48, so Alaska, Hawaii and the territories blow the maximum
out by 70×. A check written on the median would have passed; the max is what notices. Web Mercator
is off by 62% at the median and 702% at worst, which is `.area` on a geographic frame reprojected
for web tiles — the single most common way a correct library returns a meaningless number.

**Moran's I has no closed-form reference value, so assert an invariance instead.** I is a ratio of
spatial covariance to variance of the *same* centred variable, so it is unchanged by any
non-degenerate affine transform of that variable — including a sign flip. That makes
`I(y) == I(3.7y + 112.5)` a bit-identical equality rather than a band, and an implementation that
normalised incorrectly would not satisfy it. Observed `|diff| = 0.00e+00` for both transforms.
The weights checks are the same spirit: shared-edge contiguity is symmetric by construction, so
the directed-link count must be *even*, and row-standardisation makes every row sum to 1, so the
total must be exactly `n`. Integer and exact — no tolerance to pick.

The 126 island-state and territory counties are dropped before contiguity (AK, HI, AS, GU, MP, PR,
VI) rather than carried as neighbourless features, so `no_islands == 0` is a real assertion instead
of a count of things we already knew were disconnected.

Three least-squares routes — statsmodels, scikit-learn's LAPACK path, and the normal equations
through `lstsq` — agree to **8.9e-15** on the same real design. The tolerance is conditioning, not
taste.

### Pins (data tier: durable government source)

| | |
|---|---|
| image | `quay.io/aarchsci/geo-ml@sha256:ed8c59dedeb4…` — geopandas 1.1.4, shapely 2.1.2, pyproj 3.7.2, libpysal 4.15.0, esda 2.10.0, scikit-learn 1.9.0, statsmodels 0.15.0 |
| counties | `tl_2024_us_county.zip`, `sha256:04e668d35027…` (83,913,260 B) from `www2.census.gov/geo/tiger/TIGER2024/COUNTY/` |

TIGER is versioned by year on a stable path, so `TIGER2024` is a durable id. The zip travels as one
flat file and geopandas reads it in place, so there is no directory to stage
([why](../../practices/container-path.md)).

### Run + verify

```sh
make stage RECIPE=geo-ml
spawn task run --spec "$(make -s spec RECIPE=geo-ml)" --wait
make ls RECIPE=geo-ml
```

The smoke check runs inside the task and the bucket listing is the second half of it
([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with
`geodesic_area_vs_census` under 1e-5. Re-running overwrites the prefix; no spec edit needed.

</details>
