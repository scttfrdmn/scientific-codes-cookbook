#!/usr/bin/env python3
"""xarray + dask on a real workload: one full year (2023) of hourly ERA5 2 m temperature at 0.25 deg.

What a climate researcher actually runs: open a year of hourly reanalysis lazily, compute an
area-weighted global mean, daily and monthly means, a diurnal and a monthly climatology, and
anomalies against them. 8,760 x 721 x 1,440 = 9.09e9 values, 36.4 GB as float32.

Every claim asserted here holds for a reason other than "it was observed once":

 1. THE DATA IS THE PUBLISHED DATA. Each staged file matches its sha256 (checked before this
    runs); 12 files, 8,760 hours, no gaps, no duplicates, every day exactly 24 hours.

 2. AREA WEIGHTS ARE EXACT. A 0.25 deg cell between latitudes p_s and p_n has area
    R^2 dlon (sin p_n - sin p_s); the sum over the grid telescopes to 4 pi R^2. Asserted to
    1e-12 relative (721 float64 terms), and the normalised weights sum to 1.

 3. RESAMPLE CONSERVES. Time-averaging and area-averaging are both linear, so they commute:
    the global mean of the daily-mean field equals the daily mean of the hourly global mean,
    and day-weighted monthly means and the daily means each reproduce the annual-mean FIELD,
    elementwise, in every one of 1,038,240 cells (a total would be transpose-invariant).

 4. ANOMALIES AVERAGE TO ZERO over their climatology period, elementwise, for both the diurnal
    (hour of day) and the monthly climatology; the diurnal one is computed two ways (groupby,
    and a (day, hour) reshape) and they must agree. A control subtracts the
    climatology one hour out of phase; it must miss by kelvins, so the check has teeth.

 5. DASK == EAGER. Every month is re-read without dask and its hourly global-mean series and
    monthly-mean field must match the dask results. Not bit-identical in general (a chunked
    reduction sums in a different order), so the bound is 1e-12 relative, ~4,500 ulp.

 6. THE PUBLISHED NUMBERS. Copernicus C3S, from ERA5: 2023 global mean 14.98 C (Global Climate
    Highlights 2023); July 2023 16.95 C (July 2023 bulletin); the record daily global mean
    17.08 C on 6 July 2023 (C3S/WMO, within 0.01 C of 5 and 7 July). Published to two
    decimals, so +-0.005 for rounding, and C3S quoted 2023 from ERA5T, revised since: the bound
    is +-0.01 C.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import glob
import hashlib
import os
import resource
import sys
import time

import numpy as np

ROWS = []
T_START = time.time()


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def dump():
    with open("era5-results.tsv", "w") as fh:
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    dump()
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            die("%s is not finite" % name)


def bound(name, value, tol):
    finite(name, value)
    rec(name, "%.3e" % value)
    if not value <= tol:
        die("%s = %.3e exceeds %.1e" % (name, value, tol))


def rel(a, b):
    a, b = np.asarray(a, "f8"), np.asarray(b, "f8")
    return float(np.max(np.abs(a - b)) / np.max(np.abs(b)))


def h(a):
    a = np.ascontiguousarray(a)
    return hashlib.sha256(a.tobytes()).hexdigest()[:16]


def rss_gib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 ** 2  # Linux: KiB


def timed(label, fn):
    t0 = time.time()
    out = fn()
    rec("wall_%s_s" % label, "%.1f" % (time.time() - t0))
    return out


import dask
import xarray as xr

THREADS = int(os.environ.get("ERA5_THREADS", "8"))
DATA = os.environ.get("ERA5_DIR", "/tmp/era5")
dask.config.set(scheduler="threads", num_workers=THREADS)
ver("xarray", lambda: xr.__version__)
ver("dask", lambda: dask.__version__)
ver("numpy", lambda: np.__version__)
ver("arch", lambda: os.uname().machine)
ver("threads", lambda: THREADS)
try:
    from threadpoolctl import threadpool_info
    rec("threadpools", "; ".join("%s %s %s n=%s" % (i.get("internal_api"), i.get("version"),
                                                 i.get("architecture"), i.get("num_threads"))
                                 for i in threadpool_info()))
except Exception as exc:
    rec("threadpools", "unknown (%s)" % type(exc).__name__)

FILES = sorted(glob.glob(os.path.join(DATA, "*.nc")))
FULL = len(FILES) == 12
rec("files", len(FILES))
if not FILES:
    die("no netCDF staged under %s" % DATA)

# ---------------------------------------------------------------- 1. the data
CHUNKS = {"time": 24, "latitude": -1, "longitude": -1}   # one day per chunk, from 00 UTC
ds = xr.open_mfdataset(FILES, combine="by_coords", chunks=CHUNKS, engine="netcdf4",
                       data_vars="minimal", coords="minimal", compat="override", parallel=False)
T = ds["VAR_2T"]
rec("shape", "x".join(map(str, T.shape)))
nt = T.sizes["time"]
times = T["time"].values
steps = np.unique(np.diff(times).astype("timedelta64[h]").astype(int))
rec("time_steps_h", ",".join(map(str, steps)))
if list(steps) != [1]:
    die("time axis is not strictly hourly: steps %s" % steps)
if FULL and (nt != 8760 or str(times[0])[:13] != "2023-01-01T00" or str(times[-1])[:13] != "2023-12-31T23"):
    die("a full 2023 is 8760 hours 2023-01-01T00..2023-12-31T23, got %d %s..%s" % (nt, times[0], times[-1]))
if T.shape[1:] != (721, 1440):
    die("grid is %r, ERA5 0.25 deg is 721 x 1440" % (T.shape[1:],))

# ---------------------------------------------------------------- 2. area weights
R = 6371008.8                                       # IUGG mean Earth radius, m
lat = T["latitude"].values.astype("f8")
nlon = T.sizes["longitude"]
north = np.clip(lat + 0.125, -90, 90)
south = np.clip(lat - 0.125, -90, 90)
band = R ** 2 * (2 * np.pi / nlon) * (np.sin(np.radians(north)) - np.sin(np.radians(south)))
area_total = band.sum() * nlon
bound("area_sum_rel_err_vs_4piR2", abs(area_total - 4 * np.pi * R ** 2) / (4 * np.pi * R ** 2), 1e-12)
w = xr.DataArray(band / area_total, dims="latitude", coords={"latitude": T["latitude"]})
bound("weights_sum_minus_1", abs(float(w.sum()) * nlon - 1.0), 1e-12)
coslat = xr.DataArray(np.cos(np.radians(lat)), dims="latitude", coords={"latitude": T["latitude"]})


def gmean(field, weights=w):
    return field.weighted(weights).mean(("latitude", "longitude"))


# ---------------------------------------------------------------- pass 1: one graph, one read
X = T.astype("f8")
g_hourly = gmean(X)
g_hourly_cos = gmean(X, coslat)
daily = X.resample(time="1D").mean()
n_per_day = X["time"].resample(time="1D").count()
monthly = X.resample(time="MS").mean()
n_per_month = X["time"].resample(time="MS").count()
clim_month = X.groupby("time.month").mean()
clim_hour = X.groupby("time.hour").mean()
annual = X.mean("time")
t0 = time.time()
(g_h, g_hc, D, nd, M, nm, CM, CH, A) = dask.compute(
    g_hourly, g_hourly_cos, daily, n_per_day, monthly, n_per_month, clim_month, clim_hour, annual)
rec("wall_pass1_dask_s", "%.1f" % (time.time() - t0))
rec("rss_after_pass1_gib", "%.2f" % rss_gib())
finite("pass-1 results", g_h, D.values, M.values, CH.values, A.values)

nd_v = nd.values
rec("days", len(nd_v))
if FULL and len(nd_v) != 365:
    die("2023 has 365 days, got %d" % len(nd_v))
if not (nd_v == 24).all():
    die("a day does not have exactly 24 hours: %s" % np.unique(nd_v))

# ---------------------------------------------------------------- 3. resample conservation
g_d = gmean(D).values
bound("commute_global_of_daily_vs_daily_of_global_rel",
      rel(g_d, g_h.values.reshape(-1, 24).mean(axis=1)), 1e-12)
Av = A.values
nm_v = nm.values.astype("f8")
from_months = (M.values * nm_v[:, None, None]).sum(axis=0) / nm_v.sum()
bound("annual_field_from_monthly_rel_max_cell", rel(from_months, Av), 1e-12)
bound("annual_field_from_daily_rel_max_cell", rel(D.values.mean(axis=0), Av), 1e-12)
bound("groupby_month_vs_resample_MS_rel", rel(CM.values, M.values), 1e-12)
bound("annual_global_hourly_vs_field_rel", rel(float(g_h.mean()), float(gmean(A))), 1e-12)

# ---------------------------------------------------------------- 4. anomalies (passes 2a, 2b)
# Diurnal: reshape time into (day, hour) -- each 24-hour chunk is one day from 00 UTC -- so the
# anomaly against the hour-of-day climatology streams day by day. The obvious spelling,
# `X.groupby("time.hour") - clim`, was OOM-killed at 64 GiB (twice, within seconds of starting):
# without flox each hour group is a strided slice of every chunk. Monthly groups are contiguous,
# so they keep the groupby spelling, in a separate compute so a failure names its pass.
Xc = X.coarsen(time=24).construct(time=("day", "hour"))
CHn = xr.DataArray(CH.transpose("hour", "latitude", "longitude").values,
                   dims=("hour", "latitude", "longitude"))
anom_h = Xc - CHn
ctrl = Xc - CHn.roll(hour=1)                                          # one hour out of phase
t0 = time.time()
(CHc, AH, ACTRL, GA) = dask.compute(Xc.mean("day"), anom_h.mean("day"), ctrl.mean("day"),
                                    gmean(anom_h).mean())
rec("wall_pass2a_diurnal_s", "%.1f" % (time.time() - t0))
rec("rss_after_pass2a_gib", "%.2f" % rss_gib())
finite("diurnal anomalies", CHc.values, AH.values, ACTRL.values, GA.values)
bound("clim_hour_groupby_vs_reshape_rel",
      rel(CHc.transpose("hour", "latitude", "longitude").values, CHn.values), 1e-12)
bound("anom_hour_mean_max_abs_K", float(np.abs(AH.values).max()), 1e-9)
bound("anom_global_mean_abs_K", abs(float(GA)), 1e-9)
ctrl_max = float(np.abs(ACTRL.values).max())
rec("control_out_of_phase_max_abs_K", "%.3f" % ctrl_max)
if not ctrl_max > 1.0:
    die("the out-of-phase control did not miss (max %.3e K), so the zero check has no teeth" % ctrl_max)
t0 = time.time()
AM = (X.groupby("time.month") - CM.chunk()).groupby("time.month").mean().compute()
rec("wall_pass2b_monthly_s", "%.1f" % (time.time() - t0))
rec("rss_after_pass2b_gib", "%.2f" % rss_gib())
finite("monthly anomalies", AM.values)
bound("anom_month_mean_max_abs_K", float(np.abs(AM.values).max()), 1e-9)

# ---------------------------------------------------------------- 5. dask == eager, every month
t0 = time.time()
worst_series, worst_field, ident = 0.0, 0.0, 0
for i, f in enumerate(FILES):
    with xr.open_dataset(f, engine="netcdf4") as one:
        x = one["VAR_2T"].astype("f8").load()          # no dask anywhere on this path
    sl = slice(int(nm_v[:i].sum()), int(nm_v[:i + 1].sum()))
    ge = gmean(x).values
    worst_series = max(worst_series, rel(ge, g_h.values[sl]))
    fe = x.values.mean(axis=0)
    worst_field = max(worst_field, rel(fe, M.values[i]))
    ident += int(h(ge) == h(g_h.values[sl]))
    del x
rec("wall_pass3_eager_s", "%.1f" % (time.time() - t0))
rec("rss_after_pass3_gib", "%.2f" % rss_gib())
bound("dask_vs_eager_hourly_global_rel", worst_series, 1e-12)
bound("dask_vs_eager_monthly_field_rel", worst_field, 1e-12)
rec("dask_vs_eager_hourly_series_bit_identical_months", "%d of %d" % (ident, len(FILES)))

# ---------------------------------------------------------------- 6. results, and the published ones
C = 273.15
ann = float(g_h.mean()) - C
ann_equal_months = float(gmean(M).mean()) - C
ann_cos = float(g_hc.mean()) - C
gm = gmean(M).values - C
rec("global_mean_2023_C", "%.4f" % ann)
rec("global_mean_2023_equal_month_weights_C", "%.4f" % ann_equal_months)
rec("global_mean_2023_coslat_weights_C", "%.4f" % ann_cos)
rec("global_monthly_C", ",".join("%.3f" % v for v in gm))
rec("annual_mean_hash", h(Av))
rec("hourly_global_hash", h(g_h.values))
k = int(np.argmax(g_d))
dmax_date = str(D["time"].values[k])[:10]
rec("daily_global_max_C", "%.4f %s" % (g_d[k] - C, dmax_date))
rec("daily_global_min_C", "%.4f %s" % (g_d.min() - C, str(D["time"].values[int(np.argmin(g_d))])[:10]))
rec("field_annual_min_max_K", "%.2f %.2f" % (Av.min(), Av.max()))
if FULL:
    for name, got, want in (("published_2023_annual", ann, 14.98),
                            ("published_july_2023", float(gm[6]), 16.95),
                            ("published_daily_record", float(g_d[k] - C), 17.08)):
        rec(name + "_diff_C", "%+.4f" % (got - want))
        if not abs(got - want) <= 0.01:
            die("%s: %.4f C vs C3S's %.2f C" % (name, got, want))
    if dmax_date not in ("2023-07-05", "2023-07-06", "2023-07-07"):
        die("the record day is %s; C3S put it on 6 July, within 0.01 C of 5 and 7 July" % dmax_date)

rec("rss_peak_gib", "%.2f" % rss_gib())
rec("wall_total_s", "%.1f" % (time.time() - T_START))
np.save("annual_mean_2023.npy", Av.astype("f4"))
np.save("global_hourly_2023.npy", g_h.values)
dump()
print("ERA5 REAL OK", flush=True)
