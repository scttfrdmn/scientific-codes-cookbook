---
tool: pdal
env: pointcloud
image: quay.io/aarchsci/pointcloud@sha256:1e53023e25dc060315e5e693be3f942aa733c4e7ca6cab65fa915a34e2862823
env_x86_64: pointcloud-792583991793
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# PDAL (pointcloud env) — decode the canonical autzen cloud, header vs a decode statistic

`pdal` reads and decodes a real LiDAR point cloud — the ingest step of any point-cloud pipeline.

> **What this covers.** One real cloud (10.6M points), verified by structure + a decoded statistic — proof PDAL's native LAZ decode and stats engine work on Graviton4. Not a benchmark; no filtering pipeline, terrain product, or tiled/streaming workflow. The domain's second stage-and-pin recipe over real data (with [earth-observation](../earth-observation/README.md)).

## Run it

```bash
make stage RECIPE=pointcloud
spawn task run --spec "$(make -s spec RECIPE=pointcloud)" --wait
pdal info --summary autzen.laz               # count 10,653,336; bounds
pdal info --stats --dimensions Z autzen.laz  # mean Z 434.1025 over all points
```

One task. The cloud is staged from PDAL's own data repo and pinned.

## Which box — [measured](../../measurements/pointcloud-real/README.md) (a whole 3DEP project, 4.64 G points)

| chip | instance | PDAL wall | $/hr | compute $ | **billed $/result** |
|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 647 s | 0.6381 | **0.115** | **0.230** |
| Xeon 6975P-C | `c8i.4xlarge` | 820 s | 0.7497 | 0.171 | 0.287 |
| EPYC 9R45 | `c8a.4xlarge` | **486 s** | 0.8622 | 0.116 | 0.274 |

USGS's `CA_LassenNP_2020`, read in place from RODA's EPT, is cut into 2 km tiles; each tile is exported to LAZ and gridded to a 2 m ground DTM, 16 `pdal pipeline --stream` jobs at a time. **PDAL keeps all 16 vCPU busy** (15.5 cores), so cores are the dial, not the data path. AMD is fastest, Graviton4 cheapest. Point counts, class histograms (PDAL against laspy), the LAS round trip and the DTM's ground-point conservation hold exactly on all three chips. The written LAZ bytes vary from run to run, because `readers.ept`'s point order does.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `autzen.laz` — PDAL's canonical 10.6M-point cloud | your own LAZ/LAS | autzen's documented count (**10,653,336**) is both a header value *and* a citable reference identity, so a swap loses that free published-number check. |
| `pdal info --stats` | a real `pdal pipeline` (filters, DEM, tiling) | this reads and summarizes; a production run stages a pipeline JSON and its outputs through S3. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** 10.6M real points already exercise the full LAZ decode, and the mean-Z identity is exact; a bigger cloud is a longer run, not a more legible one. Leave-it. A whole project at scale is [measured](../../measurements/pointcloud-real/README.md).

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Measured phase split: stage-in of the 54 MB cloud **1 s**, Docker install **38 s**, image pull **76 s**, decoding 10.6M points and computing stats **7 s**. Provisioning is 94% of the 123 s window ([why](../../practices/what-this-does-not-cover.md)), so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)). The cloud stages into `/tmp` — [tmpfs at ½ the instance RAM](../../patterns/data-movement.md), not the root disk — which is trivial at this size but is what sizes the box at real scale.

**Sizing:** a tiled, streamed pipeline over a whole project is compute-bound (15.5 of 16 vCPU, 1.4 GiB per job, [measured](../../measurements/pointcloud-real/README.md)). Size on cores, read the EPT in place, and keep tile outputs on the root disk, not the tmpfs ([data movement](../../patterns/data-movement.md)).

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

| observable | assertion | arm64 | x86_64 |
|---|---|---|---|
| pinned sha256 | matches | OK | OK |
| **point count** | exactly 10,653,336 (canonical autzen, header) | 10653336 | 10653336 |
| bounds X / Y / Z | 635577.79…639003.73 / 848882.15…853537.66 / 406.14…615.26 | matches | matches |
| decoded-point count | == header count (stats pass saw every point) | 10653336 | 10653336 |
| **mean Z (decode)** | 434.1025 (mean over all decoded points) | 434.1025 | 434.1025 |

**On x86_64** (`c8i.large`, the `pointcloud-792583991793` twin: PDAL 2.10.1 on both, but this older pin solves to 197 of 245 versions identical on linux-64, including hdf5 1.14.6 vs 2.1.0, libarrow 22 vs 24 and scipy 1.18.1 vs 1.18.0, all listed in the lock header): `smoke-check.txt` is **byte-identical** to the Graviton run. Every value here is a header field or a decode statistic reported at four decimals.

The count and bounds are the header cross-check; **mean Z is the decode identity** — PDAL must LZ-decompress and apply the header's scale/offset to all 10.6M points to reproduce 434.1025, which neither the header nor the sha256 proves. `z_stat_count` equaling the header count confirms the whole cloud was decoded, not a prefix.

### Run + verify

```sh
make stage RECIPE=pointcloud    # once; fetch + verify + upload autzen.laz (~56 MB)
make run RECIPE=pointcloud
make run RECIPE=pointcloud ARCH=x86_64   # same spec, x86 twin, writes runs-x86_64/
make ls RECIPE=pointcloud
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect three objects (`summary.json`, `zstats.json`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
