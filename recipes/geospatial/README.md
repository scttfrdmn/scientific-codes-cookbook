---
tool: gdal
env: geospatial
image: quay.io/aarchsci/geospatial@sha256:1827aeb547547b3e7ced8ede5cde82563635a765c2d5455221347ee2c10a74fb
spawn_version: 0.104.0
---
# GDAL/PROJ/GEOS core (geospatial env) — reproject, geometry, and a raster round-trip

The shared geospatial core (PROJ, GEOS, GDAL, rasterio, shapely, pyproj) reprojects coordinates, computes geometry, and round-trips a raster — the foundation every GIS tool sits on.

> **What this covers.** The *shared core* the `geospatial`, `earth-observation`, `geo-ml`, and `pointcloud` envs all build on — GDAL/PROJ/GEOS + rasterio/shapely/pyproj — on small synthetic data. Proof it's correct on Graviton4; not a benchmark and not a large real raster or full EO workflow. It's the domain's foundational recipe.

## Run it

```python
import pyproj, shapely, rasterio
pyproj.Transformer.from_crs(4326, 3857).transform(-83, 40)   # → -9239517.74, 4865942.28 m
shapely.Polygon([(0,0),(1,0),(0,1)]).area                    # → 0.5 exactly (GEOS)
# write a 4×4 GeoTIFF with rasterio, read it back → bit-identical, GDAL opens the same file
```

One task, one `python3` invocation. Every input is generated in memory, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| synthetic coordinate / polygons / 4×4 raster | your real coordinates + imagery | generating the inputs is what makes the checks exact and reproducible; a real EO recipe stages imagery (see [earth-observation](../earth-observation/README.md), [pointcloud](../pointcloud/README.md)) and gets its identities from the data instead. |
| WGS84 → Web Mercator of (−83°, 40°) | your CRSs + coordinates | the expected easting/northing are a standard PROJ transform — a wrong PROJ data path lands elsewhere. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** each identity is exact at this size and the raster round-trip is bit-checkable; a large raster is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The work is ~1 s, single-threaded. Recorded command window **60s** — the shortest in the cookbook, thanks to the ~0.37 GB `geospatial` image (the smallest env); boot, Docker install, and that pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: four kinds of identity, the interop cross-check, pins, smoke check, run + verify</summary>

### The checks — four kinds of identity, no bare thresholds

- **Reference projection** — WGS84 → Web Mercator of (−83°, 40°) is a deterministic PROJ transform with a known answer (`−9239517.74, 4865942.28` m in EPSG:3857).
- **Round-trip conservation** — 4326 → 3857 → 4326 recovers the coordinate to < 1e-6°; reprojection is invertible, and asserting it is stronger than the forward transform alone.
- **Geometric identity** — a right triangle with legs 1 has area exactly 0.5 (GEOS).
- **Raster conservation + interop** — a 4×4 uint8 raster written with rasterio reads back **bit-identical**, keeps its CRS, and GDAL opens the same file at the same dimensions. Two independent libraries agreeing on the bytes is an [interop cross-check](../../practices/cross-checks.md), not just "a file was written".

### Pins (data tier: synthetic / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/geospatial@sha256:1827aeb547547b3e7ced8ede5cde82563635a765c2d5455221347ee2c10a74fb` (tag `2026.09.03`, GDAL/PROJ/GEOS + rasterio/shapely/pyproj/scikit-image, cosign-signed, `linux/arm64`) |
| input | synthetic (coordinate, polygons, 4×4 raster), **generated in-task** — nothing staged |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| Web Mercator easting / northing | −9239517.74 / 4865942.28 ± 1 m (EPSG:3857 of −83°,40°) | −9239517.74 / 4865942.28 |
| **reproject round-trip** | 4326→3857→4326 recovers (−83,40) ± 1e-6° | (−83.0, 40.0) |
| **triangle area** | exactly 0.5 (GEOS) | 0.5 |
| buffer is a disk | unit-buffer area 3.10–3.15 (≈ π) | 3.136548 |
| **raster round-trip** | read-back == written (bit-identical), CRS EPSG 4326 | True / 4326 |
| GDAL interop | GDAL opens the rasterio file, 4×4, checksum ≥ 0 | 4×4, chk 89 |

The buffer area is banded (shapely's default segmentation approximates π as ~3.1365, version-dependent), so it's a "this is a disk" check; the rest are exact or reference values.

### Run + verify

```sh
make run RECIPE=geospatial
make ls RECIPE=geospatial
```

a completed run does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect four objects (`geo-results.txt`, `geo-results.json`, `tiny.tif`, `smoke-check.txt`). Re-run: bump the `-r1` suffix.

</details>
