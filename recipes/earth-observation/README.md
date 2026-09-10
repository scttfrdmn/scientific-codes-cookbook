---
tool: rasterio
env: earth-observation
image: quay.io/aarchsci/earth-observation@sha256:8e011f38aea1fa3ad1ce6b40208bf1e6afd69a0848f212b05236e01fbf1a5337
spawn_version: 0.104.0
---
# rasterio (earth-observation env) — a real Sentinel-2 COG, checked against its STAC metadata

`rasterio` / `rioxarray` / GDAL open a real Sentinel-2 scene; the check is that the raster's CRS, dtype, shape, transform and nodata match what the scene's published STAC item declares, plus a deterministic pixel checksum that proves GDAL actually *decoded* the imagery.

> **What this covers.** One 60 m band of one real Sentinel-2 L2A scene, verified against published metadata — proof the GDAL/rasterio/rioxarray COG path decodes real imagery on Graviton4. Not a benchmark, not a full EO analysis (no time series, mosaic, or ML); the domain's first stage-and-pin recipe over real data.

## Run it

```python
import rasterio
with rasterio.open("S2B_11SKA_20240704_0_L2A_B01.tif") as ds:
    ds.crs, ds.dtypes[0], ds.shape, ds.transform, ds.nodata   # must equal the STAC item
    ds.read(1)                                                 # decodes to GDAL checksum 55297
```

One task. The scene is staged from AWS Open Data and its expected header values come from the scene's own STAC item.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the pinned scene `S2B_11SKA_20240704_0_L2A` B01 (1830² uint16 COG) | your own scene / band | one 60 m band is enough to prove the decode path; the identities come from the *scene's* STAC item, so a different scene needs its own declared metadata. |
| the STAC-declared header values (asserted) | your scene's STAC item | **load-bearing** — the check is *pixels agree with published metadata*, so the reference must be the metadata for the exact scene staged, not a guess. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** a single real band already exercises COG decode and gives a real-data identity; a full scene or mosaic is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Opening and checksumming a 6 MB COG is ~1 s, single-threaded. Recorded command window **71s** — boot, Docker install, the ~0.53 GB image pull, and staging the ~6 MB scene are the whole task ([why](../../practices/container-path.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the STAC cross-check, the decode checksum, pins, smoke check, run + verify</summary>

### The identity — STAC metadata vs the actual pixels

Decided before writing, not during (the [reproduce-a-published-reference](../../practices/reference-from-tests.md) discipline, here against a scene's STAC item rather than a test suite): **assert that the fetched raster's header matches what the STAC item declares, and that GDAL decodes it to a fixed checksum.** "The scene opened and had a shape" is a threshold; this is exact-or-wrong on six axes plus a pixel-level check.

The scene's STAC item (earth-search, `sentinel-2-l2a`) declares for B01: `proj:epsg` 32611, `proj:shape` [1830, 1830], `proj:transform` [60,0,199980,0,−60,4100040], `data_type` uint16, `nodata` 0. The staged asset must agree with every one. The GDAL band checksum (55297) is the piece metadata can't give — it proves arm64 GDAL **decodes** the COG's compressed pixels correctly, which the sha256 pin (byte integrity) does not.

### Pins (data tier: RODA)

| | |
|---|---|
| image | `quay.io/aarchsci/earth-observation@sha256:8e011f38aea1fa3ad1ce6b40208bf1e6afd69a0848f212b05236e01fbf1a5337` (tag `2026.09.03`, GDAL/rasterio/rioxarray/stackstac/pystac-client/xarray, cosign-signed, `linux/arm64`) |
| scene | `S2B_11SKA_20240704_0_L2A` B01 from `sentinel-cogs` (AWS Open Data, append-only) — `sha256:8626d4bb…` (6,192,186 B, 1830² uint16 COG) |

`stage-inputs.sh` fetches from `sentinel-cogs`, verifies the sum, and uploads once (a small cross-region copy).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| pinned sha256 | matches | OK |
| **CRS** | EPSG 32611 (STAC `proj:epsg`) | 32611 |
| **dtype** | uint16 (STAC `data_type`) | uint16 |
| **shape** | (1830, 1830) (STAC `proj:shape`) | (1830,1830) |
| **nodata** | 0 (STAC `nodata`) | 0 |
| **transform** | [60,0,199980,0,−60,4100040] (STAC `proj:transform`) | matches |
| **GDAL checksum** | exactly 55297 (deterministic pixel decode) | 55297 |
| rioxarray agrees | reads the same CRS (32611) | 32611 |

Six are the STAC-metadata cross-check (exact-or-wrong); the checksum is the pixel identity; reading the CRS through both rasterio and rioxarray is a light interop cross-check.

### Run + verify

```sh
recipes/earth-observation/stage-inputs.sh    # once; fetch + verify + upload B01 (~6 MB)
spawn task run --spec recipes/earth-observation/01-cog.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/earth-observation/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect one object (`smoke-check.txt`) — this recipe verifies an input rather than producing a large output. Re-run: bump the `-r1` suffix. A transient `Invalid IAM Instance Profile name` on a parallel launch is the IAM-propagation race (spore-host/spawn#572) — re-run.

</details>
