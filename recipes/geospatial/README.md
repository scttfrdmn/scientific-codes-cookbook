# geospatial — reproject, geometry, and a raster round-trip across rasterio and GDAL

One task. The core geospatial stack (PROJ, GEOS, GDAL, rasterio, shapely, pyproj)
reprojects a coordinate, computes geometry, and writes then reads back a raster — and the
smoke check confirms a reference projection, a round-trip conservation, an exact geometric
identity, and bit-identical raster I/O across two libraries.

> **What this recipe does and does not cover.** It exercises the *shared core* of the
> geospatial/EO envs — the GDAL/PROJ/GEOS + rasterio/shapely/pyproj base that
> `geospatial`, `earth-observation`, `geo-ml`, and `pointcloud` all build on — with
> small, synthetic, in-memory data. It proves the stack works correctly on Graviton4; it
> is not a benchmark and does not process a large real raster or a full EO workflow.
> **It is the first cookbook recipe in the geo/EO domain**, a block the catalog had zero
> coverage of.

## Why one task, and why nothing is staged

One environment, one coherent set of operations run in one `python3` invocation. Every
input is generated in memory — a coordinate pair, two polygons, and a 4×4 raster written
to a temp GeoTIFF — so there is **no input to stage** and no `stage-inputs.sh`; the image
digest is the only pin. (A real EO recipe would stage imagery; this opener deliberately
stays synthetic so the checks are exact and reproducible.)

## The checks: four kinds of identity, no bare thresholds

The recipe mirrors the `geospatial` env's own D3, so the numbers are comparable, and each
check is an identity rather than an observation:

- **Reference projection** — WGS84 → Web Mercator of (−83°, 40°) is a standard,
  deterministic PROJ transform with a known answer (`−9239517.74, 4865942.28` m in
  EPSG:3857). A wrong PROJ data path or a broken transform lands elsewhere.
- **Round-trip conservation** — reprojecting 4326 → 3857 → 4326 recovers the original
  coordinate to < 1e-6°. Reprojection is invertible; this asserts it, which is stronger
  than checking the forward transform alone.
- **Geometric identity** — a right triangle with legs 1 has area exactly 0.5 (GEOS).
  Exact, no tolerance.
- **Raster conservation + interop** — a 4×4 uint8 raster written with rasterio (CRS
  EPSG:4326) reads back **bit-identical**, keeps its CRS, and GDAL opens the same file at
  the same dimensions. Two independent libraries agreeing on the bytes is an interop
  cross-check, not just "a file was written".

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/geospatial@sha256:1827aeb547547b3e7ced8ede5cde82563635a765c2d5455221347ee2c10a74fb` |
| | tag `2026.09.03`, GDAL/PROJ/GEOS + rasterio/shapely/pyproj/scikit-image, cosign-signed, `linux/arm64` |
| input | synthetic (coordinate, polygons, 4×4 raster), **generated in-task** — nothing staged |

**Data tier: synthetic / in-task.** The image digest is the only pin.

This is the shared base of four EO envs; a domain recipe (STAC in `earth-observation`,
PDAL in `pointcloud`, geopandas in `geo-ml`) would layer its headline tool on top of this
same core.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| Web Mercator easting | −9239517.74 ± 1 m (EPSG:3857 of −83°,40°) | −9239517.7358 |
| Web Mercator northing | 4865942.28 ± 1 m | 4865942.2795 |
| **reproject round-trip** | 4326→3857→4326 recovers (−83,40) ± 1e-6° | (−83.0, 40.0) |
| **triangle area** | exactly 0.5 (GEOS) | 0.5 |
| buffer is a disk | unit-buffer area 3.10–3.15 (≈ π) | 3.136548 |
| **raster round-trip** | read-back == written (bit-identical) | True |
| CRS preserved | EPSG 4326 | 4326 |
| GDAL interop | GDAL opens the rasterio file, 4×4, checksum ≥ 0 | 4×4, chk 89 |

The buffer area is banded (shapely's default segmentation approximates π as ~3.1365, and
that is version-dependent), so it's a "this is a disk" check rather than an identity; the
other seven are exact or reference values.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The work is
**~1 second** and single-threaded; `c8g.large` is the smallest box.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.37 GB** `geospatial` image (the smallest env in the catalog) are the whole task. The
recorded run's command window was **60s** (23:33:35 → 23:34:35 UTC) — the shortest in the
cookbook, thanks to that small image. TTL was **retightened from that first real run**:
10m → **5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius, not
caution; the recorded run used the original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — everything is synthetic.

```sh
spawn task run --spec recipes/geospatial/01-roundtrip.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/geospatial/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect four
objects (`geo-results.txt`, `geo-results.json`, `tiny.tif`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both.

**Note on parallel launches.** If launched alongside other tasks and it dies with an AWS
`Invalid IAM Instance Profile name` error, that is a transient IAM-propagation race
(spore-host/spawn#572), not a recipe fault — no instance was created, so just re-run it.
