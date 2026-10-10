---
tool: sf-terra
tool_version: "sf 1.1.3 / terra 1.9.50"
env: r
image: quay.io/aarchsci/r@sha256:136bf063a0d04967e2e2dac8622259edf344d5aa74cdd5869502007cf6c5a56c
spawn_version: 0.126.1
last_verified: 2026-10-10
---
# sf + terra — Census's areas and ESA's class percentages, recovered in R on Graviton

Runs R's spatial stack on Graviton4 against two references published inside the data, on the exact bytes two Python recipes here already used. For anyone doing spatial work in R on ARM.

## Run it

```bash
make stage RECIPE=r-spatial     # once: the checks only — both datasets are already staged
spawn task run --spec "$(make -s spec RECIPE=r-spatial)" --wait
make ls RECIPE=r-spatial

v <- sf::st_read("/vsizip//tmp/tiger.zip")          # 3235 counties, EPSG:4269
terra::expanse(terra::vect(v), unit = "m", transform = TRUE)   # ellipsoidal; matches ALAND+AWATER
r <- terra::rast("/tmp/SCL.tif")                    # 5490² @ 20 m, EPSG:32611
terra::freq(r)                                      # class counts -> ESA's published percentages
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `terra::expanse(transform=TRUE)` | `sf::st_area` | **this is the whole point.** `st_area` on lon/lat uses **s2 — a sphere** — and lands **10,800× further** from Census. This env has no `r-lwgeom`, so `sf_use_s2(FALSE)` gives you planar, not geodesic. |
| EPSG:5070 | your own equal-area CRS | measured as the subtle trap: its *median* error matches geodesic (5.4e-08) but its *max* is 68× worse — fine mid-projection, wrong at the edges. |
| TIGER counties | any polygons with a published area | the reference ships **inside** the `.dbf` as `ALAND`/`AWATER`, so there is no version to match. |
| `terra::freq` | `terra::global`, `zonal` | class counts are integers, so the comparison against ESA is exact arithmetic rather than a tolerance. |
| reading the siblings' objects | your own staging | both inputs are read from [geo-ml](../geo-ml/README.md) and [earth-observation](../earth-observation/README.md). **Don't copy them** — the cross-check only means something on identical bytes. |

**Leave both fixtures.** They are chosen so the answer is already published: Census computed the areas, ESA computed the percentages. **Scale it** by pointing the same two legs at your own data — but you lose the reference, which is the part that makes this more than a smoke test.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 40m as a **backstop** with `cost_limit` $0.20 as the real guard. Peak RSS **3,791 MiB**, so `m8g.large` would be tight — tmpfs takes half of RAM. The analysis is **34 s** of a 1m43s window; 37 s is installing Docker and 28 s pulling R, the catalog's largest image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: two published references recovered to the same residual as the Python stacks, and a 10,800× discrimination control</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| shared inputs | sha256 equal the siblings' pins | 3 of 3 |
| R / sf / terra / s2 | read from the running install | **4.5.3** / 1.1.3 / 1.9.50 / 1.1.11 |
| GEOS / GDAL / PROJ | — | 3.14.1 / 3.13.3 / 9.9.0 |
| counties | exactly 3235 | 3235 |
| CRS | EPSG:4269, as TIGER ships | 4269 |
| sf → terra handoff | geometry count preserved | 3235 |
| **ellipsoidal area vs Census** | **max rel err < 1e-5 over 3235** | **6.710e-07** (median 5.242e-08) |
| **total area vs Census** | **rel err < 1e-6** | **6.372e-08** |
| **spherical is further off** | **≥ 1000×** | **10,800×** |
| equal-area projection | *reported* | max 4.535e-05, median 5.436e-08 |
| SCL cells | exactly 30,140,100 (5490²) | 30,140,100 |
| grid | 20 m, EPSG:32611, origin (199980, 4100040) | matches |
| **class counts partition the grid** | **sum == ncell exactly** | **30,140,100** |
| **ESA's class percentages** | **max\|diff\| < 2e-6 pp over 12** | **9.642e-07 pp** |
| **2× aggregate conserves pixels** | **count unchanged exactly** | **11,729,019 → 11,729,019** |

### Neither leg is a tool-vs-tool comparison, deliberately

terra and rasterio both decode through GDAL, so their agreeing would say little about either. What
both legs do instead is recover a number a **third party** published and shipped inside the data —
and because a Python recipe here already recovers it from the same object, the R result is an
independent stack measured against a common reference:

| | R here | Python sibling | reference |
|---|---|---|---|
| county area, max rel err | **6.710238e-07** | 6.710e-07 ([geo-ml](../geo-ml/README.md)) | Census `ALAND`+`AWATER` |
| county area, median | 5.242285e-08 | 5.2e-08 | ” |
| total area, rel err | **6.372129e-08** | 6.372e-08 | ” |
| ESA class %, max diff | **9.642e-07 pp** | 9.64e-07 pp ([earth-observation](../earth-observation/README.md)) | STAC item |

**Both stacks land on the same residual, to four significant figures on the vector side.** That is
what a correctly computed shared reference looks like: the residual is Census's own precision —
integer m² on counties of order 1e9 m² — not a property of either toolchain. The bound on the
raster leg is likewise set by ESA printing 6 decimals, so a half-ulp is 5e-7 pp
([tolerances come from the problem](../../practices/cross-checks.md)).

This recipe checks **12** SCL codes where earth-observation checks 11: its class map omits code 1,
`saturated_defective`, which is 0 px in this scene. A superset, not a correction.

### The sphere/ellipsoid control, which is the real lesson

`sf::st_area` on a lon/lat object is **spherical** — s2 is a sphere — and Census `ALAND` is
**ellipsoidal**. Reaching for the obvious function gives a plausible number that is wrong in the
third decimal place:

```text
terra::expanse(transform = TRUE)   ellipsoidal   max rel 6.710e-07   median 5.242e-08
sf::st_area() with s2              spherical     max rel 7.247e-03   median 9.328e-04
st_area(st_transform(v, 5070))     planar/EA     max rel 4.535e-05   median 5.436e-08
```

The spherical route is **10,800× further** from Census, and that ratio is asserted — it is what
makes the agreement a statement about getting the model right rather than a coincidence. Without
it, "our areas match Census" is unfalsifiable prose.

**The equal-area projection is the subtle one.** Its median error (5.436e-08) is as good as the
geodesic route, so a spot check passes; its maximum is **68× worse**, because EPSG:5070 is a CONUS
projection and TIGER includes Alaska, Hawaii and the territories. Reported rather than asserted —
it is a correct tool used outside its valid area, which is a different failure from a wrong model.

**This env has `r-s2` but no `r-lwgeom`**, so `sf_use_s2(FALSE)` does not buy a geodesic fallback —
it drops to planar on geographic coordinates. That is why the ellipsoidal route here is terra's
`expanse()`, and why it was measured in a probe before anything was written against it.

### Two identities that need no reference

**The classification partitions the grid.** Every pixel gets exactly one class, so the counts must
sum to the cell count with nothing left over — 30,140,100, exact integers, no tolerance. A read
that dropped or double-counted a block fails this regardless of what the percentages look like.

**Aggregation conserves pixels.** Summing a binary vegetation mask over 2×2 blocks cannot create or
destroy pixels, so the total survives a change of grid exactly: **11,729,019** at 5490², the same
**11,729,019** at 2745². An integer identity across a real resampling operation.

### Pins

| | |
|---|---|
| counties | `inputs/geo-ml/tiger.zip`, `sha256:04e668d35027…` (83,913,260 B) — **read, not copied** |
| SCL band | `inputs/earth-observation/SCL.tif`, `sha256:258f36e59c6c…` (2,571,146 B) — read, not copied |
| STAC item | `inputs/earth-observation/item.json`, `sha256:053ecac854fb…` (22,880 B) — **the raster reference** |
| checks | `identities.R`, pinned by sha256 |
| image | `quay.io/aarchsci/r@sha256:136bf063a0d0…` — R 4.5.3, sf 1.1.3, terra 1.9.50, s2 1.1.11, GEOS 3.14.1, GDAL 3.13.3, PROJ 9.9.0 |

Staging pins the two shared inputs to the **full** sha256 of the objects those recipes staged, so
if either re-stages a different file this fails locally rather than silently cross-checking
against something else. It also confirms the references are still *inside* the data before an
instance is paid for — that the TIGER `.dbf` header still carries `ALAND`/`AWATER`, and that the
STAC item still declares the class percentages — because a pin fixes bytes, not meaning.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=r-spatial
spawn task run --spec "$(make -s spec RECIPE=r-spatial)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/r-spatial/r1/score.tsv" -
```

Fails on a pin mismatch, a shared input whose sha256 is not the sibling's, a county count that is
not 3235, an area more than 1e-5 from Census, a spherical model that is *not* clearly worse, a grid
that is not 5490² at 20 m, class counts that do not partition it, any ESA percentage off by 2e-6 pp,
or an aggregate that changes the pixel count — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

`terraOptions(progress = 0)` is set deliberately: terra's progress bar writes to stdout and
interleaves into the task log, mangling the line beside it, and a diagnostic has to stay readable
to be worth staging out.

### Not covered

**The rest of this env, which is most of it** — `r-tidyverse`, `r-data.table`, `r-arrow`,
`r-glmnet`, `r-randomforest`, `r-caret`, `r-knitr`/`r-rmarkdown`/`pandoc` are all present and
unexercised here; only `sf` and `terra` are. `r-arrow` in particular would pair naturally with the
geo stack and is untouched.

Also: writing vector or raster formats (this recipe only reads), `sf`'s geometric predicates and
overlays, CRS transformation accuracy as a subject in its own right (PROJ's own test suite is
exercised by [geospatial](../geospatial/README.md) instead), rasters that do not fit in memory —
terra's windowed reading is its real strength and 30 M cells does not require it — and any
performance claim, since this is one box at one size.

</details>
