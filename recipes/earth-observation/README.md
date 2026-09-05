# earth-observation — a real Sentinel-2 COG, cross-checked against its STAC metadata

One task. `rasterio`/`rioxarray`/GDAL open a staged Sentinel-2 scene, and the smoke check
confirms the raster's CRS, dtype, shape, transform and nodata match what the scene's STAC
item declares — plus a deterministic pixel checksum.

> **What this recipe does and does not cover.** It reads one 60 m band of one real
> Sentinel-2 L2A scene and verifies it against published metadata — enough to prove the
> GDAL/rasterio/rioxarray COG path decodes real imagery correctly on Graviton4. Not a
> benchmark, and not a full EO analysis (no time series, mosaic, or ML); it's the domain's
> **first stage-and-pin recipe over real data**, the counterpart to the synthetic
> `recipes/geospatial` and `recipes/geo-ml`.

## Why this shape, and why the input is staged

The synthetic recipes get exact identities for free by constructing their inputs. Real EO
can't: the identities have to come from the **data**. So this recipe stages a real scene
and pins it, exactly as `recipes/relion` and `recipes/siesta` stage-and-pin — the shape
the geo/EO push needs for its imagery envs.

The scene is **S2B_11SKA_20240704_0_L2A** — Sentinel-2 L2A over central California,
2024-07-04, ~0.0005% cloud — from the AWS Open Data `sentinel-cogs` bucket (RODA,
append-only, so a scene path is immutable). Its 60 m coastal-aerosol band (B01) is a
1830×1830 uint16 COG, ~6 MB.

## The identity: STAC metadata vs the actual pixels

Decided before writing, not during (the SIESTA-pseudopotential discipline): **assert that
the fetched raster's header matches what the scene's STAC item declares, and that GDAL
decodes it to a fixed checksum.** "The scene opened and had a shape" is a threshold; this
is exact-or-wrong on six axes plus a pixel-level check.

The scene's STAC item (earth-search, `sentinel-2-l2a`) declares for B01: `proj:epsg`
32611, `proj:shape` [1830, 1830], `proj:transform` [60,0,199980,0,−60,4100040],
`data_type` uint16, `nodata` 0. The recipe asserts the staged asset agrees with every one
of those — *metadata says X, the pixels agree*. The GDAL band checksum (55297) is the
piece metadata can't give: it proves arm64 GDAL **decodes** the COG's compressed pixels to
the right values, which the sha256 pin (byte integrity) does not.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/earth-observation@sha256:8e011f38aea1fa3ad1ce6b40208bf1e6afd69a0848f212b05236e01fbf1a5337` |
| | tag `2026.09.03`, GDAL/rasterio/rioxarray/stackstac/pystac-client/xarray, cosign-signed, `linux/arm64` |
| scene | `S2B_11SKA_20240704_0_L2A` B01, from `sentinel-cogs` (RODA), byte for byte |
| | `sha256:8626d4bb645a0ec92ba0099b2c6aab94d0ee328a12ada9190d1c2d0d1771a3f0` (6,192,186 B, 1830² uint16 COG) |

**Data tier: RODA.** An AWS Open Data bucket, copied byte for byte, so the sha256 is a pin
on the upstream object itself. `stage-inputs.sh` fetches from `sentinel-cogs`, verifies the
sum, and uploads it once (a small cross-region copy). The expected header values are the
scene's STAC-declared metadata, cited in the recipe.

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| pinned sha256 | matches | OK |
| **CRS** | EPSG 32611 (STAC `proj:epsg`) | 32611 |
| **dtype** | uint16 (STAC `data_type`) | uint16 |
| **shape** | (1830, 1830) (STAC `proj:shape`) | (1830,1830) |
| band count | 1 | 1 |
| **nodata** | 0 (STAC `nodata`) | 0 |
| **transform** | [60,0,199980,0,−60,4100040] (STAC `proj:transform`) | matches |
| **GDAL checksum** | exactly 55297 (deterministic pixel decode) | 55297 |
| rioxarray agrees | rioxarray reads the same CRS (32611) | 32611 |

Six of these are the STAC-metadata cross-check (exact-or-wrong); the checksum is the pixel
identity; and reading the CRS through both rasterio and rioxarray is a light interop
cross-check within the EO stack.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. Opening and
checksumming a 6 MB COG is **~1 second**, single-threaded.

**These timings are not compute cost.** Boot, the Docker install, pulling the **~0.53 GB**
`earth-observation` image, and staging the ~6 MB scene are the whole task. The recorded
run's command window was **71s** (00:18:34 → 00:19:45 UTC), the header matching STAC and
the GDAL checksum reproducing 55297. TTL was **retightened from that first real run**:
10m → **5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius, not
caution; the recorded run used the original 10m. Disk is trivial.

## Running it

```sh
recipes/earth-observation/stage-inputs.sh    # once; fetch + verify + upload B01 (~6 MB)
spawn task run --spec recipes/earth-observation/01-cog.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/earth-observation/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect one
object (`smoke-check.txt`) — this recipe verifies an input rather than producing a large
output.

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous record. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
no instance was created, so re-run.
