# climate — xESMF conservative regrid conservation, plus a real-netCDF reader path

One task. The climate stack reads a real NCEP netCDF, conservatively regrids a field, and
computes a thermodynamic quantity — and the smoke check confirms a conservation law, a
deterministic reader statistic, and a textbook identity.

> **What this recipe does and does not cover.** It exercises the reader path on a real
> netCDF, the xESMF/ESMF regridding engine, and a MetPy calculation — enough to prove the
> climate stack works correctly on Graviton4. Not a benchmark; no large dataset, GRIB
> decode, or full reanalysis workflow. It closes the geo/EO domain as the fifth env.

## Three checks, three different kinds

- **xESMF conservative-regrid conservation (the headline).** On global periodic grids a
  conservative scheme reproduces a constant field *exactly* — that is the conservation law
  (area-weighted integral preserved, no flux lost) made into an exact-or-wrong check:
  regrid a field of 1.0 from a 5°×4° global grid to an 8°×6° one, and every target cell
  must still be 1.0. This is the same class as `recipes/ambertools`' NVE energy
  conservation, and it exercises the real ESMF machinery rather than a lookup.
- **netCDF reader path on a real file.** The env's own D3 only round-trips a
  self-generated netCDF; climate users hit the *reader* on real files. So this opens a
  pinned NCEP air-temperature file (`netcdf4` engine) and asserts its known structure
  (variable `air`, shape (2920, 25, 53)) and a deterministic statistic (global mean
  281.255037 K) — a value that is wrong if the reader mis-decodes.
- **MetPy identity (companion).** Wind speed of (3, 4) m/s is exactly 5 — a units-aware
  calculation with a textbook answer.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/climate@sha256:5b3d840e79eabaa222b8766f08e998dea87313696da6cafd8a9740cde69b9ac7` |
| | tag `2026.09.02`, xarray/dask/netCDF4/xESMF/esmpy/cfgrib/eccodes/cartopy/metpy, cosign-signed, `linux/arm64` |
| netCDF | `air_temperature.nc` from `pydata/xarray-data` (commit `2baf0c22`), byte for byte |
| | `sha256:c606b89c35970a2983b914b76df4adbb409003ef34aa7cfd7f582e41f307482b` (7,751,328 B, NCEP `air`, (2920,25,53)) |

**Data tier: stable public source with a durable id.** A file at a pinned commit, pinned
by sha256. `stage-inputs.sh` fetches/verifies/uploads it once (~7 MB). The regrid and
MetPy checks are synthetic/in-code (no staging).

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| pinned sha256 | matches | OK |
| netCDF variable | `air` | air |
| netCDF shape | (2920, 25, 53) | (2920, 25, 53) |
| **netCDF mean** | 281.255037 K (deterministic reader statistic) | 281.255037 |
| **regrid conservation** | constant field → [1.0, 1.0] on the target grid | [1.0, 1.0] |
| MetPy wind speed | 5.0 (3-4-5) | 5.0 |

No thresholds: the mean and wind speed are reference values, the regrid is exact
conservation, the shape is exact.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The reader, regrid and
calc together take **~2 seconds**.

**These timings are not compute cost.** Boot, the Docker install, pulling the **~0.57 GB**
`climate` image, and staging the ~7 MB netCDF are the whole task. The recorded run's
command window was **74s** (00:44:12 → 00:45:26 UTC), the constant field regridding to
[1.0, 1.0] and the netCDF mean reading 281.255037. TTL was **retightened from that first
real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius,
not caution; the recorded run used the original 10m. Disk is trivial.

## Running it

```sh
recipes/climate/stage-inputs.sh       # once; fetch + verify + upload air_temperature.nc (~7 MB)
spawn task run --spec recipes/climate/01-regrid.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/climate/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect one
object (`smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous record. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
no instance was created, so re-run.
