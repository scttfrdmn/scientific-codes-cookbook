---
tool: pdal
env: pointcloud
image: quay.io/aarchsci/pointcloud@sha256:1e53023e25dc060315e5e693be3f942aa733c4e7ca6cab65fa915a34e2862823
spawn_version: 0.104.0
---
# PDAL (pointcloud env) — decode the canonical autzen cloud, header vs a decode statistic

`pdal` reads and decodes a real LiDAR point cloud — the ingest step of any point-cloud pipeline.

> **What this covers.** One real cloud (10.6M points), verified by structure + a decoded statistic — proof PDAL's native LAZ decode and stats engine work on Graviton4. Not a benchmark; no filtering pipeline, terrain product, or tiled/streaming workflow. The domain's second stage-and-pin recipe over real data (with [earth-observation](../earth-observation/README.md)).

## Run it

```bash
pdal info autzen.laz --stats     # count 10,653,336; bounds; mean Z 434.1025 over all points
```

One task. The cloud is staged from PDAL's own data repo and pinned.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `autzen.laz` — PDAL's canonical 10.6M-point cloud | your own LAZ/LAS | autzen's documented count (**10,653,336**) is both a header value *and* a citable reference identity, so a swap loses that free published-number check. |
| `pdal info --stats` | a real `pdal pipeline` (filters, DEM, tiling) | this reads and summarizes; a production run stages a pipeline JSON and its outputs through S3. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** 10.6M real points already exercise the full LAZ decode, and the mean-Z identity is exact; a bigger cloud is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Decoding 10.6M points and computing stats is ~4 s. Recorded command window **80s** — boot, Docker install, the ~0.64 GB `pointcloud` image pull, and staging the ~56 MB cloud are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.** (The ~56 MB cloud stages into `/tmp`, a tmpfs sized to ½ the instance RAM, not the root disk — trivial at this size.)

<details>
<summary>As shipped: the header-vs-decode identity, pins, smoke check, run + verify</summary>

### Why staged, and why a decode statistic

The `pointcloud` env's own D3 uses PDAL's synthetic `faux.reader`; a real recipe needs a real cloud, staged and pinned, with identities from the data. Header integrity (count, bounds, scale/offset) is exact-or-wrong but it's all **metadata** — it doesn't prove PDAL decoded the actual points on arm64. So, exactly as the GDAL checksum did for [earth-observation](../earth-observation/README.md), this pairs the header assertions with a **statistic over the decoded points** — the mean Z over all 10.6M. Metadata says N points in these bounds; the decoded points agree. The autzen count is also a [citable reference number](../../practices/reference-from-tests.md) (PDAL's documented 10,653,336).

### Pins (data tier: stable public source with a durable id)

| | |
|---|---|
| image | `quay.io/aarchsci/pointcloud@sha256:1e53023e25dc060315e5e693be3f942aa733c4e7ca6cab65fa915a34e2862823` (tag `2026.09.03`, PDAL + laspy + richdem, cosign-signed, `linux/arm64`) |
| cloud | `autzen.laz` from `PDAL/data` (Git LFS, commit `360327d2`) — `sha256:944b9475…` (56,350,988 B, 10,653,336 points) |

`stage-inputs.sh` fetches, verifies and uploads it once (~56 MB).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| pinned sha256 | matches | OK |
| **point count** | exactly 10,653,336 (canonical autzen, header) | 10653336 |
| bounds X / Y / Z | 635577.79…639003.73 / 848882.15…853537.66 / 406.14…615.26 | matches |
| decoded-point count | == header count (stats pass saw every point) | 10653336 |
| **mean Z (decode)** | 434.1025 (mean over all decoded points) | 434.1025 |

The count and bounds are the header cross-check; **mean Z is the decode identity** — PDAL must LZ-decompress and apply the header's scale/offset to all 10.6M points to reproduce 434.1025, which neither the header nor the sha256 proves. `z_stat_count` equaling the header count confirms the whole cloud was decoded, not a prefix.

### Run + verify

```sh
recipes/pointcloud/stage-inputs.sh    # once; fetch + verify + upload autzen.laz (~56 MB)
spawn task run --spec recipes/pointcloud/01-decode.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/pointcloud/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect three objects (`summary.json`, `zstats.json`, `smoke-check.txt`). Re-run: bump the `-r1` suffix.

</details>
