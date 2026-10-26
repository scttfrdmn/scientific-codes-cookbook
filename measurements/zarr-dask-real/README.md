# zarr + dask on a real workload — 165 years of daily CMIP6 temperature, Graviton4 vs Intel vs AMD

> **Pangeo's Zarr store of GFDL-CM4's historical run decodes to exactly the bytes ESGF's netCDF
> files hold, and survives seven rewrites bit for bit, on all three chips.** Every float result
> moves with the chunk layout and not with the CPU. AMD is fastest and cheapest per result, then
> Graviton4. Intel is last.

Workload: **CMIP6 `CMIP.NOAA-GFDL.GFDL-CM4.historical.r1i1p1f1.day.tas.gr1`, version 20180701**:
daily near-surface air temperature for 1850–2014, 60,225 days × 180 × 288. That is 12.49 GB as
float32. On AWS Open Data it is a 7.78 GB Zarr v2 store of 72 blosc-lz4 chunks (`s3://cmip6-pds`).
The ESGF netCDF it was converted from is 9 files, 7.18 GB (`s3://esgf-world`). One variable × one
experiment × one member is the unit the CMIP6 archive is cut in, and the unit an analysis opens. A
multi-model ensemble is N of these fanned out, not a different workload.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/zarr-dask-real/stage-inputs.sh "$COOKBOOK_BUCKET"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py climate $a "$COOKBOOK_BUCKET" r3 measurements/zarr-dask-real/01-ncref.task.json)" --wait
  spawn task run --spec "$(python3 scripts/spec_arch.py zarr-dask $a "$COOKBOOK_BUCKET" r3 measurements/zarr-dask-real/02-zarr.task.json)" --wait
done
bash measurements/zarr-dask-real/collect.sh "$COOKBOOK_BUCKET" r3
```

There are two tasks per chip, one tool each, with S3 between them. **`01-ncref`** runs the
[climate](../../recipes/climate/README.md) env's netCDF4 over the 9 ESGF files. It writes the
array's sha256, every (day, latitude-row) sum as an exact integer, and the coordinates. **`02-zarr`**
runs the recipe's own `pathology` image: zarr 3.4.1, dask 2026.8.0, numcodecs 0.17.0. It reads the
Zarr store, checks it against that reference, then has dask rewrite the whole array at seven
layouts and reduce each one. The x86 legs use the two envs' linux-64 twins, with zarr, dask,
numcodecs and netCDF4 at the same versions.

## Results

`real.py` uses 16 dask threads on a `4xlarge` (16 vCPU, 128 GiB) per chip; the netCDF read is
single-threaded on an `xlarge`:

| chip | instances | tool wall (netCDF + zarr) | billed (both) | peak RSS | $/hr (zarr box) | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.xlarge` + `r8g.4xlarge` | 40 + 263 s | 179 + 352 s | 46.9 GiB | 0.9426 | 0.0715 | 0.104 |
| Xeon 6975P-C | `r8i.xlarge` + `r8i.4xlarge` | 54 + 319 s | 128 + 391 s | 45.4 GiB | 1.1114 | 0.1026 | 0.131 |
| **EPYC 9R45** | `r8a.xlarge` + `r8a.4xlarge` | **31 + 182 s** | **91 + 243 s** | 45.8 GiB | 1.2781 | **0.0674** | **0.094** |

A *result* is the whole verified run: both tasks and every check below. **Billed** is spawn's task
window, wrapper start to stage-out done, from its `cost_estimate`. $/hr is that estimate divided by the billed window.

- **AMD finishes 1.45× faster than Graviton4 and 1.75× faster than Intel.** That beats AMD's 36%
  higher hourly price, so AMD is 10% cheaper per result billed than Graviton4 and 28% cheaper than
  Intel. AMD's 16 vCPUs are 16 cores (`Thread(s) per core 1`), like Graviton4's. Intel's 16 are 8
  cores × 2 SMT threads.
- **The phases split the chips differently.** Intel is *faster* than Graviton4 on the
  single-threaded reads, 1.45 s against 2.18 s for one point's 165 years from the published
  chunks. It is 1.4–2.0× *slower* on the 16-thread dask writes and reductions. This workload is
  lz4/blosc decode and encode plus float64 sums, so per-core speed and real cores both count.
- **Memory picks the box, not the cores.** The process peaks at 45–47 GiB, while the sharded copy's
  3,650-day shards are in flight. Staging lives in tmpfs (½ RAM), and the 7.8 GB store plus up to
  two 7.7 GB copies sit on top of that. That rules out a 64 GiB box.
- **These timings are not compute cost in general.** Boot, Docker, image pull and staging are 28–43%
  of the bill. Stage-in of the 7.8 GB store takes 22–25 s on every chip. Every copy is
  written to and read from local tmpfs, not S3, so the read timings below measure decode and chunk
  geometry, not request latency.
- n = 1 per cell. Every output is deterministic and identical across chips (below), so a repeat
  would only re-measure timing noise.

## What the chunk shape costs

These are the same 12.49 GB at five of the seven layouts, Graviton4. The read columns average 3
seeded points and 3 seeded days:

| layout | data objects | GB on disk | dask write | read one point's 165 years | read one day's map |
|---|---|---|---|---|---|
| published v2, `(838, 180, 288)` blosc-lz4 | 72 | 7.78 | — | — | — |
| v3, same chunks, blosc-lz4 | 72 | 7.71 | 3.9 s | 2.19 s | 0.21 s |
| v3, same chunks, zstd (no shuffle) | 72 | **10.08** | 5.6 s | 2.53 s | 0.23 s |
| v3 time series, `(60225, 18, 32)` | 90 | 7.66 | 4.3 s | **0.16 s** | 2.19 s |
| v3 ragged, `(1000, 37, 41)` | 2,440 | 7.77 | 6.0 s | 0.04 s | 0.03 s |
| v3 sharded, `(365, 45, 72)` in `(3650, 180, 288)` | **17** | 7.67 | 6.0 s | 0.07 s | **0.01 s** |

- **The published layout is a map layout.** Reading one grid point's history decodes all 72
  chunks, 12.5 GB, to keep 241 KB: 2.19 s on local disk. Rechunking for time series flips that
  13.5× one way and 10.6× the other. It is a choice of workload, not of storage.
- **Sharding gets small-chunk reads out of 17 objects.** Inner chunks of 4.7 MB make both reads
  fast, and the shard count is what an object store bills GETs and listings on. The 2,440-object
  ragged store reads as fast but has 144× the objects.
- **A codec default is not a neutral choice.** zarr's v3 `ZstdCodec` on its own applies no byte
  shuffle, and is **31% larger** than blosc-lz4-with-shuffle on the same float32 data.
- **The published bytes are not today's bytes.** Re-encoding with the store's own codec settings
  (`blosc`, lz4, clevel 5, shuffle) reproduces **0 of 72** published chunk objects, and comes out
  0.9% smaller. The 2020 store was written by an older c-blosc. A v2 rewrite and a v3 rewrite with
  today's library agree on **72 of 72** chunk payloads. Decoded, all three are bit-identical, which
  is what was asserted.

## What held at scale, on all three chips

Each of the 92 checks passed on every chip. The `v3-*` rows hold for each of the seven layouts:

| claim | why it must hold | observed (identical on all chips) |
|---|---|---|
| 9 netCDF files match ESGF's published SHA256; 123 Zarr objects match the publisher's single-part MD5 ETags | provenance | 9/9, 123/123 |
| **Zarr store == ESGF netCDF: sha256 of the whole decoded array** | lossless conversion, read by two libraries | `c69468cd…2fd90` both |
| every (day, lat-row) sum of `tas × 2²⁴` == netCDF's, all 10.8M | every float32 ≥ 0.5 is an integer at that scale, so these sums are exact and order-free | equal; total 14,496,831,323,174,129,664 / 2²⁴ |
| time, time_bnds, lat/lon and bounds == netCDF's | same coordinates through two encodings | exact; zarr's `days since 12:00` int == netCDF's float − 0.5, all 60,225 |
| 72 chunk objects, the last ragged; noleap 165 × 365 | the store's geometry | 71 × 838 + 727 |
| cell weights `sin φn − sin φs` telescope to 2 | contiguous bounds pole to pole | 0.0 |
| **`areacella` == the spherical cell area at R = 6371 km, rounded to float32** | it is stored as float32, so one rounding: ≤ 2⁻²⁴ | max 5.33e-8 ≤ 5.96e-8, all 51,840 cells |
| dask annual coarsen == numpy, every cell of 165 × 51,840; years sum to each cell's total | integer sums are exact | exact |
| `v3-*`: data objects == the grid's chunk/shard count | the layout is what was asked for | 72 / 72 / 72 / 90 / 2,440 / 17 / 72 |
| `v3-*`: **bit-identical to the published store**, region by region on the copy's own grid | the chunk shape is a storage decision | every region, every layout |
| `v3-*`: point series and day maps == numpy | reads that cross chunks | exact |
| `v3-*`: exact integer (day, row) sums == reference; min/max bit-exact; argmax of the unique maximum == numpy | integer addition and comparisons are order-free | exact; index 2,867,408,966 |
| `v3-*`: **every daily float64 sum is exact** | all values ≥ 128 K are multiples of 2⁻¹⁶ and a day's partial sums stay < 2³⁷, so float64 can round nothing | 0 error, 60,225 days × 7 layouts |
| `v3-*`: float64 total within `2·log₂(n)·ε` = 1.40e-14 of the exact value | blocked summation's backward-error bound, n = 3.12e9 | **0 ulp** from correctly rounded at every layout |
| `v3-*`: **control, float32 accumulation misses that bound** | the bound has teeth | 2.1e-8 to 9.7e-8 out: the global mean off by 6e-6 to 2.7e-5 K |
| `v3-*`: every daily area-weighted sum within `(2·log₂(51840)+2)·ε` of an `fsum` over exact row sums | one rounding per product plus the tree | max 3.56e-16 ≤ 7.40e-15 |
| the same dask reduction twice | determinism | bit-identical |

GFDL-CM4's own global-mean `tas` is **285.917 K for 1850–1900 and 286.636 K for 1995–2014: 0.72 K
of historical warming** on this member. It is reported, not asserted. There is no published value
for this member to check it against.

## Across chips: fixed points identical, and the paths too

Every exact quantity is the same on all three chips. These are the array's sha256, the 10.8M
integer row sums, min/max/argmax and the coarsened years. So is every **path**, and that is the
real finding. The float32 total takes 3 distinct values across the 7 layouts, and the
area-weighted daily series takes 4. Each layout's value is **bit-identical on Graviton4, Intel and
AMD** (`compare.txt`). The order of addition is set by the chunk grid and by numpy's pairwise
kernel, not by the CPU. The [fixture](../../recipes/zarr-dask/README.md) found the same at 512 × 512.
Nothing here calls a large blocked GEMM, so OpenBLAS's per-host kernel choice (`neoversev2` on
Graviton4, `Cooperlake` on both x86 chips) cannot move it.

The float64 *daily* sums cannot differ by layout either: they are exact by construction. That is
why [climate-real](../climate-real/README.md) saw byte-identical ERA5 reductions on two chips. A
float32 field reduced in float64 rounds nothing until a partial sum outgrows 53 bits.

## Pins

| | |
|---|---|
| data, Zarr | `s3://cmip6-pds/CMIP6/CMIP/NOAA-GFDL/GFDL-CM4/historical/r1i1p1f1/day/tas/gr1/v20180701/` (102 objects) and `…/fx/areacella/gr1/v20180701/` (21), AWS Open Data, us-west-2, **Payer: BucketOwner**. Pinned in `tas.md5` / `areacella.md5` by the publisher's single-part ETags, which are MD5s of the bytes. `stage-inputs.sh` diffs the live listing against them, and the task runs `md5sum -c`. RODA tier |
| data, netCDF | `s3://esgf-world/CMIP6/…/v20180701/*.nc`, 9 files, us-east-2. Pinned in `esgf-nc.sha256` by the SHA256 the ESGF index publishes for each file, identical at all four replicas it lists. Checked in-task |
| scripts | `ncref.py`, `real.py`, `run.sh`, in `pins.sha256`, checked in-task |
| images | `climate@sha256:5b3d840e…` / twin `climate-60e8dca4df0d`, and the recipe's `pathology@sha256:9c7f7713…` / twin `pathology-c817c8dd8540` |

Raw outputs per chip are in `results/`. The staged copy is 14.96 GB in your bucket (~$0.34/month);
delete `inputs/zarr-dask-real/` when done.
