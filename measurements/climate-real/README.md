# xarray + dask on a real workload — a year of hourly ERA5, Graviton4 vs x86

> **A full year of hourly ERA5 at 0.25° reproduces Copernicus's published 2023 numbers to
> 0.003 °C on both chips, and every non-timing output is byte-identical across them.**
> Graviton4 finishes 1.13× faster and 20% cheaper per result billed.

Workload: **all 8,760 hours of 2023** of ERA5 2 m temperature on the native 721 × 1,440 grid —
9.09e9 values, 36.4 GB as float32, 13.35 GB as the 12 monthly netCDF4 files NSF NCAR publishes on
AWS Open Data. Opened lazily with `xr.open_mfdataset`, reduced with dask on 8 threads: the
area-weighted global mean, daily and monthly means, a diurnal and a monthly climatology, anomalies
against both, and the same monthly reductions re-run eagerly. One hourly variable for one year is
the unit ERA5 users actually work in (it is how the archive is cut); a 30-year climatology is 30 of
these, fanned out, not a different workload.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/climate-real/stage-inputs.sh "$COOKBOOK_BUCKET"
spawn task run --spec "$(python3 scripts/spec_arch.py climate arm64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/climate-real/era5-2023.task.json)" --wait
spawn task run --spec "$(python3 scripts/spec_arch.py climate x86_64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/climate-real/era5-2023.task.json)" --wait
```

One task per chip runs `era5.py` (pinned by sha256) over the staged year. The x86 leg is the
recipe's `climate` env twin: xarray 2026.7.0, dask 2026.8.0, numpy 2.5.2 on both.

## Results

8 threads, one `r8.2xlarge` (64 GiB) per chip, same versions, same input bytes:

| chip | instance | read passes: dask ×3 / eager | era5.py wall | $/hr | compute $/result | billed | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.2xlarge` | 239 / 240 / 237 / 163 s | 884 s | 0.4713 | 0.1157 | 1068 s | **0.1398** | 30.9 GiB |
| Xeon 6975P-C | `r8i.2xlarge` | 266 / 269 / 255 / 206 s | 1001 s | 0.5557 | 0.1545 | 1129 s | **0.1743** | 32.7 GiB |

A *result* is the whole verified year: every reduction and check below. **Billed** is launch to
terminate.

- **Graviton4 is 1.13× faster on every pass and 20% cheaper billed** (25% on compute alone). The
  run is four full reads of the year, each ~4 minutes; the work per pass is netCDF4 decode plus a
  float64 reduction, on both chips alike.
- **Compute-dominated**: boot, Docker, image pull and staging are 2–3 minutes of an 18–19 minute
  bill. Staging 13.35 GB from the bucket into `/tmp` took 32 s (Graviton4) and 41 s (x86).
- **Memory picks the family, not the cores.** Staging lives in tmpfs (½ RAM), so 13.35 GB of
  input needs a 32 GiB `/tmp` before any compute, and the run peaks at 31–33 GiB on top: `r8g`.
  A `c8g.2xlarge` (16 GiB) cannot stage the year at all.
- **Align dask chunks to the file's chunks.** These files store 27-hour HDF5 chunks. Pass 1 took
  **132 s** with 54-hour dask chunks and **239 s** with the 24-hour chunks used here (which the
  day × hour reshape needs) — a chunk split across two dask tasks is decompressed twice. n = 1 each.
- n = 1 per cell. Every output is deterministic and identical across chips (below), so a repeat
  would only re-measure timing noise.

## What held at scale, on both chips

| claim | why it must hold | Graviton4 | x86 |
|---|---|---|---|
| 12 files match their sha256; 8,760 hours, step 1 h, 365 days × exactly 24 | provenance, no gaps | ok | ok |
| cell areas `R²Δλ(sin φn − sin φs)` sum to 4πR²; normalised weights sum to 1 | telescoping sum | 0.0e+00 | 0.0e+00 |
| global mean of the daily field == daily mean of the hourly global mean | averaging operators commute | 2.9e-15 | 2.9e-15 |
| day-weighted monthly means → annual-mean field, every one of 1,038,240 cells | resample conserves | 1.8e-16 | 1.8e-16 |
| daily means → annual-mean field, every cell | resample conserves | 2.4e-15 | 2.4e-15 |
| `groupby("time.month")` == `resample(time="MS")` | same bins, two code paths | 0 | 0 |
| hour-of-day climatology: `groupby` == (day, hour) reshape | same bins, two code paths | 0 | 0 |
| diurnal anomalies average to zero per hour, every cell | definition of an anomaly | 2.9e-14 K | 2.9e-14 K |
| monthly anomalies average to zero per month, every cell | definition of an anomaly | 2.8e-14 K | 2.8e-14 K |
| control: climatology one hour out of phase | the zero check has teeth | misses by 6.58 K | 6.58 K |
| dask == eager, hourly global series and monthly field, all 12 months | same computation | bit-identical 12/12 | 12/12 |
| 2023 global mean, C3S [Global Climate Highlights 2023](https://climate.copernicus.eu/global-climate-highlights-2023): 14.98 °C | published | 14.9829 | 14.9829 |
| July 2023, C3S [July bulletin](https://climate.copernicus.eu/july-2023-sees-multiple-global-temperature-records-broken): 16.95 °C | published | 16.953 | 16.953 |
| record daily global mean, C3S/WMO: 17.08 °C on 6 July | published | 17.0797, 6 July | same |

Bounds: `1e-12` relative on every conservation and dask-vs-eager comparison (float64 sums of at
most 8,760 terms sit thousands of ulp inside), `1e-9` K on anomaly means, `> 1` K on the control,
and **±0.01 °C on the published values** — they are quoted to two decimals (±0.005), and C3S
computed them from ERA5T, which has been revised since. Each published value is ~0.003 °C inside.

## Across chips: all fixed points, all identical

There is no path in this workload — every output is a sum — and every one agrees **byte for
byte**: the 1,551-byte results table differs only in its timing and arch lines, and the staged
annual-mean field (4.2 MB) and hourly global series have the same MD5 on both chips. The path
is numpy reductions and an `einsum` (the weighted mean), not a large blocked GEMM, so there is
nothing for OpenBLAS's per-host [kernel choice](../openblas-x86/README.md) to move — and the
identical bytes say the summation order is the same on both.

The weighting is the one choice that moves the answer: **equal-weighting the 12 monthly means
gives 14.9721 °C**, 0.011 °C off the day-weighted 14.9829 — enough to miss C3S's 14.98 at two
decimals. cos-latitude weights instead of exact cell areas give 14.9830.

## The diurnal anomaly needs a reshape, not `groupby`, at this size

`X.groupby("time.hour") - clim` followed by `.groupby("time.hour").mean()` was **OOM-killed at
64 GiB, twice, 7–70 s into the pass**, with the climatology in memory and then chunked.
The `climate` env has no `flox`, and without it each hour group is a strided slice through every
chunk. Reshaping the hourly axis into (day, hour) with
`X.coarsen(time=24).construct(time=("day", "hour"))` gives the identical climatology (asserted,
difference 0) and streams at a 31 GiB peak. Monthly groups are contiguous, so `groupby("time.month")`
arithmetic ran fine as written.

## Pins

| | |
|---|---|
| data | ERA5 `e5.oper.an.sfc.128_167_2t.ll025sc.2023MM…nc`, 12 objects from `s3://nsf-ncar-era5/e5.oper.an.sfc/2023MM/` (NSF NCAR RDA ds633.0 on the AWS Open Data Registry, us-west-2, **Payer: BucketOwner** — free to read; RODA tier). sha256 of each in `era5-2023.sha256`, checked by `stage-inputs.sh` while streaming from the source and again in-task |
| script | `era5.py`, sha256 `a1c25536…40af55` (checked in-task against the staged `pins.sha256`) |
| image | the recipe's `climate@sha256:5b3d840e…` (arm64) and its `climate-60e8dca4df0d` x86 twin |

Raw outputs per chip are in `results/`. The staged copy is 13.35 GB in your bucket (~$0.31/month
in S3 Standard); delete `inputs/climate-real/era5-2023/` when done.
