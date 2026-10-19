# geopandas + libpysal on a real workload: every US census tract, and Colorado's DEM by tract — Graviton4 vs Intel vs AMD

> **All 85,529 US census tracts get geodesic areas, a dissolve to counties, a spatial join,
> national queen and rook contiguity built two independent ways, Moran's I and four OLS routes.
> Colorado's 362.88 M-cell DEM is then summarised by its 1,447 tracts.** Every identity holds on all
> three chips. Two cross-code checks against [terra](../r-spatial-real/README.md) on the same bytes
> also hold: per-tract areas agree within 1e-3 m², and the zonal table agrees cell for cell.
> AMD is fastest and cheapest per result. The zonal comparison turned up something in terra:
> its decimetre sums are not `round(v × 10)`. They carry a float32 intermediate.

Workload, in two legs that share the tracts:
- **Vector, national:** the complete TIGER/Line 2024 tract release (56 files, 42.4 M vertices).
  Steps: geodesic area and perimeter with pyproj; Albers and Web Mercator areas; a `dissolve` to
  3,235 counties; a `sjoin` of each tract's representative point to its county; libpysal
  `Queen`/`Rook` over all 85,529 tracts, rebuilt from GEOS predicates (`sjoin(predicate="intersects")`
  and DE-9IM `relate`); esda Moran's I on log10(ALAND) with 999 seeded permutations; OLS through
  statsmodels, scikit-learn, `lstsq` and spreg.
- **Raster, one state:** the 28 Copernicus GLO-30 tiles over Colorado that
  [geospatial-real](../geospatial-real/README.md) staged, mosaicked with `gdal.BuildVRT`. Steps:
  `rasterio.features.rasterize` of the tracts and counties, then exact integer sums by `bincount`.

A national tract file is what a spatial analyst loads for a contiguity matrix or a Moran's I. Tract
weights at national scale are the standard input to a spatial regression, over 27× as many
polygons as the recipe's county graph. The next unit down, the ~8 M blocks, is the same job fanned out by state.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
bash measurements/geo-ml-real/stage-inputs.sh "$COOKBOOK_BUCKET"     # stages real.py; checks the rest
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py geo-ml $a "$COOKBOOK_BUCKET" "$(date +%s)" \
    measurements/geo-ml-real/real.task.json)" --wait
done
bash measurements/geo-ml-real/collect.sh "$COOKBOOK_BUCKET" <nonce>
```

One task per chip runs [`real.py`](real.py), which is pinned by sha256 and checked in-task, as is
every input. The Intel and AMD legs use the recipe's `geo-ml-5ca5b99c000d` linux-64 twin, with
geopandas 1.1.4, shapely 2.1.2 / GEOS 3.14.1, pyproj 3.7.2 / PROJ 9.8.1, GDAL 3.12.4, libpysal 4.15.0,
esda 2.10.0 and spreg 1.9.1 on all three. `mkspec.py` writes the spec. The run needs
[r-spatial-real](../r-spatial-real/README.md) to have run on the same chip first, because terra's
outputs are the cross-code reference.

## Results

4 vCPU / 32 GiB, one `r8?.xlarge` per chip, same versions, same input bytes:

| chip | instance | real.py wall | billed | $/hr | compute $/result | **billed $/result** | peak memory |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.xlarge` | 382 s | 495 s | 0.2356 | 0.0250 | 0.0324 | 22.9 GiB |
| Xeon 6975P-C | `r8i.xlarge` | 373 s | 429 s | 0.2778 | 0.0288 | 0.0331 | 25.7 GiB |
| **EPYC 9R45** | `r8a.xlarge` | **275 s** | **324 s** | 0.3195 | **0.0244** | **0.0288** | 25.7 GiB |

A *result* is the whole workload plus every check below. *Billed* runs from EC2 launch to terminate,
at spawn's estimate. Peak memory is the container cgroup's `memory.peak`; `ru_maxrss` was 22.97 GiB
on all three. Threads per core: 1 on Graviton4 and AMD, 2 on Intel, whose 4 vCPU are 2 cores.
OpenBLAS selects `neoversev2` on Graviton4 and `Cooperlake` on both x86 chips.

| phase (s) | geodesic, tracts | dissolve | libpysal Queen + Rook | GEOS queen + rook | county graphs | zonal ×3 |
|---|---|---|---|---|---|---|
| Graviton4 | 37.0 | 52.0 | 72.8 + 77.5 | 8.7 + 24.7 | 26.3 | 15.8 |
| Intel | 29.4 | 48.2 | 79.1 + 80.4 | 8.0 + 23.1 | 25.2 | 21.6 |
| AMD | 22.4 | 39.2 | 56.2 + 55.8 | 6.3 + 17.6 | 20.2 | 13.3 |

- **AMD is 1.39× faster than Graviton4, and that beats its 1.36× hourly price.** It is 2% cheaper
  per result on compute and 11% cheaper billed. Intel matches Graviton4's wall time at an 18% higher
  hourly price, so it is 15% dearer on compute. Billed, it is only 2% dearer, because the x86 route skips the 0.9 GB image pull.
- **libpysal's contiguity builders are 40% of the run**, and they are slower than GEOS. They take
  150 s on Graviton4, while GEOS reproduces the same two graphs in 33 s. Both are single-threaded,
  so more cores would not help this workload.
- **The box is sized by memory.** The ~23–26 GiB peak comes from the raster leg (the mosaic as
  float32, two float64 decimetre layers and an int32 zone layer, 363 M cells each). On a 16 GiB
  `m8g.xlarge` this OOMs; 32 GiB is the smallest that fits. No other size was run.
- **Boot, staging, the Docker install and the image pull** take 0.8–1.9 minutes per chip.
  **These timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).
- n = 1 per cell. Graviton4 ran twice, and its tract-area CSV was byte-identical. Every asserted
  value is deterministic, so a second run would add a timing sample and nothing about correctness.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| 56 tract zips, 28 DEM tiles, the county file and `real.py` match their pins | provenance | ok | ok | ok |
| 85,529 tracts, unique 11-digit GEOIDs, EPSG:4269 | the release | ok | ok | ok |
| **geodesic area within 1 m² + perimeter × 0.0557 m of Census, every tract** | **Census's reference** ([why that bound](../r-spatial-real/README.md)) | **0 outside**; worst at 0.2173 of it | same | same |
| **pyproj area − terra area < 0.01 m², every tract** | **two codes, same bytes, same chip** | max 8.6e-4 m² | max 3.1e-5 m² | max 3.1e-5 m² |
| Σ tract areas == county area, 3,235 counties | interior edges cancel | 2.5e-16 rel | 3.8e-16 | 3.8e-16 |
| `dissolve` rebuilds every county's area | a clean coverage | 2.1e-16 rel | 2.0e-16 | 2.0e-16 |
| each tract's representative point is in exactly one county, the one its GEOID names | the ID hierarchy, by geometry | 85,529 of 85,529 | same | same |
| no two tracts' interiors meet | a coverage | 0 pairs | 0 | 0 |
| **libpysal Queen == GEOS `intersects`, pair for pair** | **vertices vs geometry** | **269,767 = 269,767** | same | same |
| **libpysal Rook == GEOS boundary∩boundary of dimension 1** | the same, for edges | **237,369 = 237,369** | same | same |
| rook ⊆ queen; queen symmetric, `s0` = 2 × pairs | by construction | ok | ok | ok |
| **tract graph collapsed by county == the county file's graph** | **a county boundary is its tracts' edges** | queen 9,480, rook 9,128, 0 differ | same | same |
| Moran's I, sign flip | negation is exact | 0.00e+00 | same | same |
| Moran's I, `3.7y + 112.5` | exact to n × ε | 5.6e-16 | same | same |
| esda Moran == `n/S0 · zᵀWz/zᵀz` (scipy sparse) | the definition | 1.0e-15 | same | same |
| `EI` == −1/(n−1), n = 85,141 | closed form | ok | ok | ok |
| four OLS routes agree | conditioning | 1.1e-11 | 6.8e-12 | 6.8e-12 |
| residual Moran's I: esda == spreg's `moran_res` | two libraries | 1.4e-14 | 1.4e-14 | 1.4e-14 |
| **rasterio cells per Colorado tract == terra's, all 1,447** | **GDAL's cell-centre rule, PROJ's same shift** | **0 differ**, 360,398,892 cells | same | same |
| rasterio decimetre sums == terra's via `round(float32(v × 10))`, all 1,447 | terra's value route, matched | 0 differ, Σ 7,541,399,513,167 dm | same | same |
| tract zones grouped by county == county zones, 64 counties | nesting | 0 differ | 0 | 0 |

Observed and identical on all three: Moran's I **0.850996647863** (z_norm 425.5); 999 seeded
permutations giving p_sim 0.001 and z_sim 414.7316; Albers median error 3.3e-07 against Web Mercator's
0.63 (max 8.66 = 866%); 433 tracts over the recipe's flat 1e-5 relative bound, for the
integer-rounding reason r-spatial-real gives. NAD83 → WGS84 moves Colorado's vertices by up to
8.003e-06°, exactly the shift terra's PROJ 9.9 applied. The raw-NAD83 control moves **913** tracts
by up to **61** cells, the same numbers as terra's.

## terra's decimetre sums carry a float32 intermediate

r-spatial-real computes `round(dem * 10, 0)` and sums integer decimetres, so that every sum is
exact. rasterio reproduces its cell counts on all 1,447 tracts. But summing `round(v × 10)`
computed in double, which is exact for a float32 `v`, misses its decimetre sums on **1,289 of 1,447
tracts**, and the state total comes out **313,590 dm lower**. Rounding the product to float32 first
reproduces terra's sums on **every** tract and its total, on all three chips. So terra's
intermediate `dem * 10` was a float32 layer (FLT4S is terra's default type for temporary files).
**314,868 of the 362.88 M cells** round differently because of it. The bias is one-sided because a
product within half a float32 step of .5 becomes exactly .5, and `std::round` sends .5 up. r-spatial-real's identities still hold, because every one of its routes reads the same
layer. What is wrong is the claim that those integers are `round(v × 10)`. If you need exact decimetres from terra,
keep the product in double, for example inside `app()`. That route was not run here.

## Across chips: fixed point or path

- **Identical on all three:** every count and every integer sum; both contiguity graphs (queen
  pairs sha256 `8db26b99…`); the county quotients; the zonal table (`co-tract-zonal.csv`, sha256
  `6ab28ab4…`); Moran's I and every seeded permutation statistic; the OLS coefficients to the
  printed nine digits.
- **Differs, Intel and AMD against Graviton4 (Intel == AMD byte for byte):** the geodesic tract
  areas (`tract-areas.csv`, written to 1e-6 m²). 83,783 of 85,529 differ, by at most 1.4e-3 m², or
  1.3e-9 relative. That is the same spread r-spatial-real measured for terra across these chips,
  and it reaches the trailing digits of the reported statistics (`geod_max_rel` 5.263060e-05 against
  5.263001e-05). Rounding-level differences also show in the OLS route spread (1.1e-11 against
  6.8e-12) and the residual Moran's I (5.6e-13). Reported, not asserted.
- **Which builds agree is itself chip-dependent.** On x86, pyproj and terra agree within 3.1e-5 m²,
  and 85,392 tracts are identical to the printed micro-m². On Graviton4 the same two codes differ by
  up to 8.6e-4 m², and only 2,272 tracts are identical. One plausible cause, untested here: aarch64
  compilers fuse `a*b+c` into FMA by default, and baseline x86-64 builds cannot. Either way, it is
  700× below Census's 1 m² and well inside the 0.01 m² claim.

## Pins

| | |
|---|---|
| tracts | Census TIGER/Line 2024 `TRACT/tl_2024_{56 FIPS}_tract.zip`, staged by [r-spatial-real](../r-spatial-real/README.md) and pinned by its `tracts.sha256`. Read, not copied |
| counties | `inputs/geo-ml/tiger.zip`, TIGER 2024 `tl_2024_us_county.zip`, `sha256:04e668d3…`. Read, not copied |
| DEM | `inputs/geospatial-real/`, 28 Copernicus GLO-30 COGs (RODA) pinned by that measurement's `tiles.sha256`. Read, not copied |
| terra reference | `runs{,-x86_64,-amd}/r-spatial-real/r1/{tract-areas,co-tract-zonal}.csv`, the same chip's; its sha256 is recorded in `results.json` |
| script | `real.py`, sha256 `b8769b88…a8da`, checked in-task |
| image | the recipe's `quay.io/aarchsci/geo-ml@sha256:ed8c59de…` (arm64) and its `geo-ml-5ca5b99c000d` x86 twin |

Raw outputs per chip are in `results/`, and `results/crosschip.txt` is the comparison. The two
CSVs stay in the bucket under `runs*/geo-ml-real/r1/`. This measurement stages nothing new except
`real.py`.
