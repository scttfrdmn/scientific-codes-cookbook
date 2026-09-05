# pointcloud — PDAL decodes the canonical autzen cloud, header cross-checked against a decode statistic

One task. PDAL reads a real LiDAR point cloud, and the smoke check confirms the header
count and bounds **and** a statistic computed over every decoded point — so the check
proves PDAL decoded the points, not just that it parsed the header.

> **What this recipe does and does not cover.** It reads one real cloud (10.6M points) and
> verifies structure + a decoded statistic — enough to prove PDAL's native LAZ decode and
> stats engine work correctly on Graviton4. Not a benchmark; no filtering pipeline, terrain
> product, or tiled/streaming workflow. It's the geo/EO domain's second stage-and-pin
> recipe (with `recipes/earth-observation`), over real data.

## Why staged, and why a decode statistic

The `pointcloud` env's own D3 uses PDAL's synthetic `faux.reader`. A real recipe needs a
real cloud, staged and pinned — and its identities must come from the data. Header
integrity (count, bounds, scale/offset) is exact-or-wrong but it's all **metadata**: it
doesn't prove PDAL LZ-decompressed and decoded the actual points correctly on arm64. So,
exactly as the GDAL checksum closed that gap for `recipes/earth-observation`, this recipe
pairs the header assertions with a **statistic over the decoded points** — the mean Z over
all 10.6M points. Metadata says N points in these bounds; the decoded points agree.

The cloud is **autzen**, PDAL's canonical reference dataset (Oregon LiDAR), whose point
count is the documented **10,653,336** — so the count is both a header value and a citable
reference identity.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/pointcloud@sha256:1e53023e25dc060315e5e693be3f942aa733c4e7ca6cab65fa915a34e2862823` |
| | tag `2026.09.03`, PDAL + laspy + richdem, cosign-signed, `linux/arm64` |
| cloud | `autzen.laz` from `PDAL/data` (Git LFS, commit `360327d2`), byte for byte |
| | `sha256:944b947501156e45df1b3b9d25bc1dc04ff5ef377e7e169576ba59231c2896ba` (56,350,988 B, 10,653,336 points) |

**Data tier: stable public source with a durable id.** A Git-LFS object at a pinned
commit, pinned by sha256. `stage-inputs.sh` fetches, verifies and uploads it once (~56 MB).

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| pinned sha256 | matches | OK |
| **point count** | exactly 10,653,336 (canonical autzen, header) | 10653336 |
| bounds X | 635577.79 … 639003.73 | matches |
| bounds Y | 848882.15 … 853537.66 | matches |
| bounds Z | 406.14 … 615.26 | matches |
| decoded-point count | == header count (stats pass saw every point) | 10653336 |
| **mean Z (decode)** | 434.1025 (mean over all decoded points) | 434.1025 |

The count and bounds are the header cross-check; **mean Z is the decode identity** — PDAL
must LZ-decompress the LAZ and apply the header's scale/offset to all 10.6M points to
reproduce 434.1025, which neither the header nor the sha256 proves. That `z_stat_count`
equals the header count confirms the stats pass decoded the whole cloud, not a prefix.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. Decoding 10.6M points
and computing stats is **~4 seconds**.

**These timings are not compute cost.** Boot, the Docker install, pulling the **~0.64 GB**
`pointcloud` image, and staging the ~56 MB cloud are the whole task. The recorded run's
command window was **80s** (00:43:26 → 00:44:46 UTC), the decode reproducing mean Z
434.1025 over all 10.6M points. TTL was **retightened from that first real run**: 10m →
**5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius, not caution; the
recorded run used the original 10m. Disk: ~56 MB cloud + image, well within the ~6.1 GiB
root.

## Running it

```sh
recipes/pointcloud/stage-inputs.sh    # once; fetch + verify + upload autzen.laz (~56 MB)
spawn task run --spec recipes/pointcloud/01-decode.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/pointcloud/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect three
objects (`summary.json`, `zstats.json`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
no instance was created, so re-run.
