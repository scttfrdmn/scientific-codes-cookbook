# sf + terra on a real workload: every US census tract, and Colorado's DEM by tract — Graviton4 vs Intel vs AMD

> **All 85,529 US census tracts are measured against Census's own areas, aggregated to their 3,235
> counties and joined back by geometry; Colorado's 362.88 M-pixel DEM is then summarised by all
> 1,447 of its tracts by two routes.** Every identity holds on all three chips, and every
> raster number is identical across them. AMD is fastest and, by 3%, cheapest per result billed. The trap this scale
> surfaced: `terra::rasterize()` does not reproject, and says nothing about it.

Workload, in two legs that share the tracts:
- **Vector:** the complete TIGER/Line 2024 census tract release, 56 state-equivalent files, 42.4 M
  vertices. Read with `sf`, then measured three ways (terra's ellipsoidal `expanse()`, `sf::st_area`
  on s2's sphere, `perim()`). Tracts are summed to counties and checked against the 2024 county file
  that [geo-ml](../../recipes/geo-ml/README.md) staged. Each tract is then spatially joined to its
  county with GEOS.
- **Raster:** the 28 Copernicus GLO-30 tiles over Colorado that
  [geospatial-real](../geospatial-real/README.md) staged (14,400 × 25,200 px, read through
  `terra::vrt`). They are summarised by Colorado's tracts and counties with
  `rasterize()` + `zonal()`, and again with per-polygon `extract()`.

Tract-level zonal statistics over a state DEM are the standard first step of an environmental
or health study at the tract level. A national tract file is the size people actually load. The
next unit down, the ~8 M blocks, is a different job, done fanned out by state.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" measurements/r-spatial-real/fetch.task.json > /tmp/f.json
spawn task run --spec /tmp/f.json --wait                     # once: Census -> your bucket, on AWS
bash measurements/r-spatial-real/stage-inputs.sh "$COOKBOOK_BUCKET"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py r-spatial $a "$COOKBOOK_BUCKET" "$(date +%s)" \
    measurements/r-spatial-real/real.task.json)" --wait
done
```

One task per chip runs [`real.R`](real.R), which is pinned by sha256 and checked in-task. The Intel
and AMD legs use the recipe's `r-d98eb9641259` linux-64 twin, with R 4.5.3, sf 1.1.3, terra 1.9.50,
GEOS 3.14.1, GDAL 3.13.3 and PROJ 9.9.0 on all three. `mkspec.py` writes both specs.

## Results

8 vCPU / 64 GiB, one `r8?.2xlarge` per chip, same versions, same input bytes:

| chip | instance | real.R wall | billed | $/hr | compute $/result | **billed $/result** | peak memory |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 485 s | 647 s | 0.4713 | 0.0635 | 0.0847 | 24.3 GiB |
| Xeon 6975P-C | `r8i.2xlarge` | 498 s | 589 s | 0.5557 | 0.0769 | 0.0909 | 27.1 GiB |
| **EPYC 9R45** | `r8a.2xlarge` | **358 s** | **462 s** | 0.6390 | 0.0635 | **0.0820** | 27.2 GiB |

A *result* is the whole workload plus every check below. *Billed* runs from EC2 launch to terminate.
Peak memory is the container cgroup's `memory.peak`. Threads per core are 1 on Graviton4 and AMD
and 2 on Intel, whose 8 vCPU are 4 cores.

| phase (s) | read 56 tract files | tract areas, 3 models + perimeter | `extract()`, 1,447 tracts | `zonal()`, tracts + counties | `rasterize()` ×2 |
|---|---|---|---|---|---|
| Graviton4 | 52.0 | 85.4 | 141.1 | 38.3 | 9.0 |
| Intel | 42.7 | 80.7 | 143.2 | 33.0 | 18.1 |
| AMD | 36.1 | 70.3 | 106.7 | 26.0 | 7.0 |

- **AMD is fastest, 1.35× Graviton4, and its 1.36× hourly price cancels that exactly.** On compute
  the two tie at $0.0635 per result. Billed, AMD is 3% cheaper ($0.0820 against $0.0847), because
  its x86 route skips the 0.86 GB image pull. Intel is 7% dearer than Graviton4 billed.
- **Graviton4 and Intel take about the same wall time**: 485 s against 498 s. Intel is faster on
  vector reads and areas, and 1.6–2.2× slower on terra's whole-raster passes (`rasterize`, the
  decimetre conversion, the valid mask, `cellSize`). It is 21% dearer per result on compute.
- **The box is sized by memory, not cores.** The 24–27 GiB peak is several 363 M-cell layers held
  at once (2.9 GB each as doubles). With ~1.5 GB staged, a 32 GiB `m8g.2xlarge` would be tight.
  Neither that size nor a core sweep was run, so this page makes no claim about threads.
- **Boot, staging, Docker and image pull are 1.5–2.7 minutes per chip.** The arm64 run pulls the 0.86 GB
  aarch.science image (21 s). The x86 runs pull micromamba (2 s) and install 328 packages offline
  (3–4 s). **These timings are not compute cost**
  ([layout](../../patterns/layout-and-effective-cost.md)).
- n = 1 per cell. Every asserted value is deterministic and identical across chips (below), so a
  second run would add a timing sample and nothing about correctness.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| 56 tract zips, 28 DEM tiles and the county file match their sha256 pins | provenance | ok | ok | ok |
| 85,529 tracts, unique 11-digit GEOIDs, all EPSG:4269 | the release | ok | ok | ok |
| **ellipsoidal area within 1 m² + perimeter × 0.0557 m of Census, every tract** | **Census's reference** | **0 of 85,529 outside**; worst at 0.217 of the bound | same | same |
| summed tract area vs Census total, relative | the same, in aggregate | 6.372e-08 | 6.372e-08 | 6.372e-08 |
| control: s2's sphere, median error over ellipsoidal's | the area check has teeth | 3,632×; 80,328 tracts outside the bound | same | same |
| **Σ tract areas == county area, all 3,235 counties** | **TIGER is one topology** | **4.5e-16** relative | 3.8e-16 | 3.8e-16 |
| Census's own tract ALAND+AWATER sum to its county figures | the reference is consistent | 3,235 of 3,235 exact | same | same |
| **each tract's interior point falls in exactly one county, the one its GEOID names** | **the ID hierarchy, by geometry** | **85,529 of 85,529** | same | same |
| tract zones partition the covered cells: counts and integer-decimetre elevation sums | exact integers in any order | 360,398,892 cells, Σ 7,541,399,513,167 dm | same | same |
| tract zones grouped by county == county zones, all 64 | nesting | 0 differ | 0 | 0 |
| **`extract()` == `rasterize()`+`zonal()`, per tract, count and sum** | **two routes, one cell-centre rule** | **1,447 of 1,447 exact** | same | same |
| control: rasterize the NAD83 polygons onto the WGS84 grid unprojected | the route check has teeth | 913 tracts differ, by up to 61 cells | same | same |
| base R `sum()` == `terra::global()` over all 362.88 M cells | exact integers | 7,569,849,656,536 | same | same |

Bounds:
- **Area, `1 m² + perimeter × 0.0557 m`.** ALAND and AWATER are integer m², so the pair is
  only good to 1 m². Census computed its areas in MAF/TIGER, from its own coordinates and with its
  own routine. Any difference of that kind moves a boundary, so it scales with **perimeter**: the
  bound allows an average boundary offset of 5.57 cm (half a micro-degree of latitude) all the way
  round. A model error scales with **area** instead, which is why the sphere fails this bound on
  94% of tracts while the ellipsoid uses at most 22% of it. The recipe's county fixture asserts
  a flat 1e-5 relative. At tract scale that fails: 433 small tracts exceed it, the worst being
  5.3e-05 on a 0.11 km² Boston tract, 5.7 m² off. It fails because 1 m² of integer rounding is
  already 9e-6 of that tract, not because the model is wrong.
- **Additivity, `< 1e-12` relative.** A county's boundary is its tracts' outer edges, vertex for
  vertex. The interior edges therefore cancel, and what remains is the order of a floating-point sum.
- The published vertices are full doubles, not a rounded copy: their distance to the nearest
  1e-6° and 1e-7° step is 0.35 and 0.46 steps.

## `rasterize()` does not reproject; `extract()` does

TIGER is NAD83 (EPSG:4269) and GLO-30 is WGS84 (EPSG:4326). Pass the NAD83 tracts straight to
`terra::rasterize(v, dem)` and it burns them in using their own coordinates, **with no warning**
(0 warnings captured). `terra::extract(dem, v)` on the same objects reprojects first and says so
(`[extract] transforming vector data to the CRS of the raster`). PROJ's NAD83→WGS84 step here
moves vertices by up to 8.0e-6° (~0.9 m). That is enough to put **913 of Colorado's 1,447 tracts
in disagreement between the two routes, by up to 61 cells**. The disagreement looks like a
membership-rule difference between the two functions, and it isn't one. After an explicit
`terra::project(v, crs(dem))`, the two routes agree on every tract, cell for cell. The habit to
keep: **project your polygons to the raster's CRS yourself before `rasterize()`**.

## R's long-double accumulators

[`patterns/r-long-double-on-arm64.md`](../../patterns/r-long-double-on-arm64.md): on arm64, R's
`long double` is software quad. **This workload does not hit it.** Its heavy steps run in C++
(terra, GEOS, geographiclib, s2), and its R-level reductions are over ≤ 85 k elements. The last block
of `real.R` times the base-R route a user might reach for instead, over the 362.88 M-cell DEM held as
an R vector:

| seconds | `sum`, whole decimetres | `sum`, ×1.000001 | `mean`, ×1.000001 | `terra::global(sum)` |
|---|---|---|---|---|
| Graviton4 | 0.28 | 3.95 | 13.23 | 4.00 |
| Intel | 0.26 | 1.27 | 6.85 | 6.11 |
| AMD | 0.13 | 2.09 | 3.38 | 3.46 |

- **The cost depends on the values.** On whole-number values, Graviton4's `sum` matches Intel's.
  On non-integers it is 3.1× slower than Intel, and `mean` is 3.9× slower than AMD. That is a
  smaller gap than the pattern page's 9–14×, which used other data.
- At this size it is seconds, not hours. The pattern page's two-hour case is `stats::cor`'s
  triple loop, which raster work does not call.

## Across chips: fixed point or path

- **Identical on all three:** every count, every integer sum, the per-tract zonal table
  (`co-tract-zonal.csv`, sha256 `664dfb26…`), the spherical-area statistics, the county means
  (San Juan 3,474.0 m), the total-area residual, and the join.
- **Differs, Intel and AMD against Graviton4 (Intel == AMD byte for byte):** terra's ellipsoidal
  tract areas (`tract-areas.csv`, written to 1e-6 m²). 84,101 of 85,529 differ in the printed
  digits, by at most 1.4e-3 m², or 1.3e-9 relative. That is a direct geodesic computation with
  nothing to converge, so it is last-bit rounding in the toolchain. It sits 700× below the 1 m² of
  Census rounding, and it moves only the trailing digits of the reported statistics
  (`geod_max_rel` 5.263066e-05 vs 5.263001e-05). Reported, not asserted. R links OpenBLAS 0.3.34, but nothing here is
  linear algebra, so its kernel (corename) was not recorded.

## Pins

| | |
|---|---|
| tracts | Census TIGER/Line 2024 `TRACT/tl_2024_{56 FIPS}_tract.zip` (422,203,224 B), a versioned release; no RODA copy exists. Fetched on AWS by `fetch.task.json` and pinned by `tracts.sha256` as fetched on 2026-10-19 |
| counties | `inputs/geo-ml/tiger.zip`, TIGER 2024 `tl_2024_us_county.zip`, `sha256:04e668d3…` — read, not copied |
| DEM | `inputs/geospatial-real/`, 28 Copernicus GLO-30 COGs pinned by that measurement's `tiles.sha256` — read, not copied |
| script | `real.R`, sha256 `90e1f6a9…eaea7ff`, checked in-task |
| image | the recipe's `quay.io/aarchsci/r@sha256:136bf063…` (arm64) and its `r-d98eb9641259` x86 twin |

Raw outputs per chip are in `results/`. The two CSVs stay in the bucket under
`runs*/r-spatial-real/r1/`. The staged tracts are 422 MB (~$0.01/month); delete
`inputs/r-spatial-real/` when done.
