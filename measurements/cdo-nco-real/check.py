#!/usr/bin/env python3
"""The checks on real.sh's products: a year of hourly ERA5 2 m temperature through CDO and NCO.

Every bound is set by the arithmetic both sides do, not by how closely they happened to agree:

 1. THE DATA. 12 staged files (sha256-checked before this runs), 8,760 hours; CDO's daily, monthly
    and hour-of-day products have 365, 12 and 24 steps.
 2. CDO'S CELL AREAS. They telescope to 4 pi R^2 (CDO's R = 6371000 m), and each is compared with
    the exact R^2 dlon (sin phi_n - sin phi_s), on the native and the 1-degree grid: the largest
    relative differences E and E1, and the L1 distance between CDO's normalised weights and the
    exact ones, are what set the remap and the CDO-vs-xarray bounds below.
 3. CDO == NCO, every cell. CDO's means are written as float64; NCO's (`ncra --dbl`) are the same
    double means rounded to the input's float32. So |cdo - nco| <= half a float32 ulp, in every
    one of 1,038,240 cells of the annual field, the 12 monthly fields and the 365 daily fields.
 4. CONSERVATION inside CDO (float64): fldmean and daymean commute; the daily means, the
    day-weighted monthly means and the 24 hour-of-day means each reproduce the annual field in
    every cell. Bounds are the worst case for naive float64 summation, n u relative for n terms
    (u = 2^-53), on both sides: TIME = 2 x 8,760 u = 1.9e-12 for a time mean, and
    FLD = 2 x 1,038,240 u = 2.3e-10 wherever a field mean (a sum over every cell) is compared.
 5. ANOMALIES. Monthly means of the daily anomalies against the monthly climatology are zero in
    every cell (1e-9 K); a control subtracting the climatology one month out of phase must miss
    by kelvins.
 6. REMAPCON CONSERVES the area integral: the global mean of every one of the 8,760 hours
    regridded conservatively to 1 degree equals the global mean on the native grid, within
    2 (E + E1): each grid's means are within 2e of exact-area means for areas within (1 +- e).
 7. CROSS-CODE WITH XARRAY, same bytes (measurements/climate-real): the hourly area-weighted
    global series, all 8,760 values, within L1 x (half the year's range): both weight sets sum to
    1, so the means differ by sum((w - w')(T - c)). The annual-mean field, every cell, within half
    a float32 ulp (xarray's was saved as float32).
 8. NCO's area-weighted mean (`ncwa -w cell_area`) == numpy's with the same weights on the same
    NCO field: ncwa writes float32, so within half a float32 ulp plus numpy's own FLD.
 9. PUBLISHED: C3S 2023 global mean 14.98 C, July 16.95 C, record daily 17.08 C on 6 July
    (within 0.01 C of 5 and 7 July); quoted to two decimals from ERA5T, so +-0.01 C.
10. THE DEFAULT SPELLINGS GIVE THE SAME ANSWER, slowly: on January, `cdo fldmean` without
    -setgridarea agrees with it within FLD, and `ncra` with the default chunk cache is
    bit-identical to `ncra --cnk_csh`.
"""
import glob
import hashlib
import sys

import numpy as np
import netCDF4

ROWS = []
U = 2.0 ** -53
NCELL = 721 * 1440
TIME = 2 * 8760 * U      # two naive float64 sums of up to 8,760 terms
FLD = 2 * NCELL * U      # two naive float64 sums over every cell


def rec(k, v):
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def dump():
    with open("cdo-nco-results.tsv", "w") as fh:
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    dump()
    sys.exit(1)


def finite(name, *vals):
    for v in vals:
        if not bool(np.all(np.isfinite(np.asarray(v, dtype="f8")))):
            die("%s is not finite" % name)


def bound(name, value, tol, fmt="%.3e"):
    finite(name, value)
    rec(name, fmt % value)
    if not bool(value <= tol):
        die("%s = %.3e exceeds %.3e" % (name, value, tol))


def rel(a, b):
    a, b = np.asarray(a, "f8"), np.asarray(b, "f8")
    return float(np.max(np.abs(a - b)) / np.max(np.abs(b)))


def var(path, name="VAR_2T"):
    with netCDF4.Dataset(path) as d:
        v = d.variables[name]
        v.set_auto_mask(False)
        return np.asarray(v[:])


def h(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def half_ulp32(a64, b32):
    """max over cells of |a64 - b32| / (half a float32 ulp at the larger of the two)."""
    a64 = np.asarray(a64, "f8")
    b32 = np.asarray(b32, "f4")
    u = np.maximum(np.spacing(np.abs(a64).astype("f4")), np.spacing(np.abs(b32))).astype("f8")
    return float(np.max(np.abs(a64 - b32.astype("f8")) / (0.5 * u))), int(np.sum(a64.astype("f4") == b32))


# ------------------------------------------------------------------ 1. shapes
g = var("g.nc").reshape(-1)
gr = var("gr.nc").reshape(-1)
ann = var("ann.nc").reshape(721, 1440)
mon = var("mon.nc").reshape(-1, 721, 1440)
day = var("day.nc").reshape(-1, 721, 1440)
dh = var("dhour.nc").reshape(-1, 721, 1440)
finite("CDO products", g, gr, ann, mon, day, dh)
rec("hours", g.size)
rec("days", day.shape[0])
rec("months", mon.shape[0])
rec("hours_of_day", dh.shape[0])
if (g.size, gr.size, day.shape[0], mon.shape[0], dh.shape[0]) != (8760, 8760, 365, 12, 24):
    die("2023 is 8760 hours, 365 days, 12 months, 24 hours of day")
for f in ("ann.nc", "mon.nc", "day.nc", "g.nc"):
    with netCDF4.Dataset(f) as d:
        if d.variables["VAR_2T"].dtype != np.float64:
            die("%s is not float64 (-b F64 was not honoured)" % f)

# ------------------------------------------------------------------ 2. CDO's cell areas
R = 6371000.0
with netCDF4.Dataset("area.nc") as d:
    area = np.asarray(d.variables["cell_area"][:], "f8").reshape(721, 1440)
    lat = np.asarray(d.variables["latitude"][:], "f8") if "latitude" in d.variables else None
if lat is None:
    lat = np.linspace(90, -90, 721)
bound("cdo_area_sum_rel_err_vs_4piR2", abs(area.sum() / (4 * np.pi * R ** 2) - 1), 1e-9)
north, south = np.clip(lat + 0.125, -90, 90), np.clip(lat - 0.125, -90, 90)
exact = R ** 2 * (2 * np.pi / 1440) * np.abs(np.sin(np.radians(north)) - np.sin(np.radians(south)))
E = float(np.max(np.abs(area / exact[:, None] - 1)))
rec("cdo_area_max_rel_err_vs_exact_E", "%.3e" % E)
w_cdo, w_ex = area / area.sum(), np.broadcast_to(exact[:, None], area.shape) / (exact.sum() * 1440)
L1 = float(np.abs(w_cdo - w_ex).sum())
rec("cdo_vs_exact_normalised_weights_L1", "%.3e" % L1)
with netCDF4.Dataset("area1.nc") as d:
    a1 = np.asarray(d.variables["cell_area"][:], "f8").reshape(180, 360)
    lat1 = np.asarray(d.variables["lat"][:], "f8")
bound("cdo_area_1deg_sum_rel_err_vs_4piR2", abs(a1.sum() / (4 * np.pi * R ** 2) - 1), 1e-9)
ex1 = R ** 2 * (2 * np.pi / 360) * np.abs(np.sin(np.radians(lat1 + 0.5)) - np.sin(np.radians(lat1 - 0.5)))
E1 = float(np.max(np.abs(a1 / ex1[:, None] - 1)))
rec("cdo_area_1deg_max_rel_err_vs_exact_E1", "%.3e" % E1)
for name, e in (("E", E), ("E1", E1)):
    if not bool(e <= 1e-4):
        die("CDO's cell areas (%s) differ from the exact spherical areas by %.3e" % (name, e))

# ------------------------------------------------------------------ 3. CDO == NCO, every cell
ann_n = var("ann_nco.nc").reshape(721, 1440)
mon_n = var("mon_nco.nc").reshape(-1, 721, 1440)
day_n = var("day_nco.nc").reshape(-1, 721, 1440)
finite("NCO products", ann_n, mon_n, day_n)
if (mon_n.shape[0], day_n.shape[0]) != (12, 365):
    die("NCO: %d monthly and %d daily means, want 12 and 365" % (mon_n.shape[0], day_n.shape[0]))
for name, a, b in (("annual", ann, ann_n), ("monthly", mon, mon_n), ("daily", day, day_n)):
    r, same = half_ulp32(a, b)
    rec("cdo_vs_nco_%s_cells" % name, a.size)
    rec("cdo_vs_nco_%s_float32_identical" % name, same)
    bound("cdo_vs_nco_%s_max_diff_in_half_ulp32" % name, r, 1.0 + 1e-6, "%.4f")

# ------------------------------------------------------------------ 4. conservation inside CDO
day_g = var("day_g.nc").reshape(-1)
bound("commute_fldmean_daymean_rel", rel(day_g, g.reshape(365, 24).mean(axis=1)), FLD)
bound("annual_from_daily_rel_max_cell", rel(day.mean(axis=0), ann), TIME)
ndays = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], "f8")
bound("annual_from_monthly_dayweighted_rel_max_cell",
      rel((mon * ndays[:, None, None]).sum(axis=0) / ndays.sum(), ann), TIME)
bound("annual_from_hour_of_day_rel_max_cell", rel(dh.mean(axis=0), ann), TIME)
bound("monthly_from_daily_rel_max_cell",
      rel(np.stack([c.mean(axis=0) for c in np.split(day, np.cumsum(ndays.astype(int))[:-1])]), mon), TIME)
ann_g = float(var("ann_g.nc").reshape(-1)[0])
bound("annual_global_vs_mean_of_hourly_global_rel", abs(ann_g - g.mean()) / ann_g, FLD)

# ------------------------------------------------------------------ 5. anomalies
an = var("anom.nc").reshape(-1, 721, 1440)
ac = var("anom_ctrl.nc").reshape(-1, 721, 1440)
finite("anomalies", an, ac)
bound("anom_monthly_mean_max_abs_K", float(np.max(np.abs(an))), 1e-9)
cm = float(np.max(np.abs(ac)))
rec("control_month_out_of_phase_max_abs_K", "%.3f" % cm)
if not bool(cm > 1.0):
    die("the out-of-phase control missed by only %.3e K: the zero check has no teeth" % cm)

# ------------------------------------------------------------------ 6. remapcon conserves
# Exact conservation on the true sphere; each grid's areas are within E (E1) of it, and a weighted
# mean of a positive field with weights within (1 +- e) is within 2e of the exact-weight mean.
REMAP_TOL = 2 * (E + E1)
rec("remapcon_tol_rel", "%.3e" % REMAP_TOL)
bound("remapcon_global_mean_rel_max_hour", float(np.max(np.abs(gr / g - 1))), REMAP_TOL)
bil = float(var("annbil_g.nc").reshape(-1)[0])
rec("remapbil_annual_global_minus_native_K", "%+.5f" % (bil - ann_g))

# ------------------------------------------------------------------ 7. cross-code with xarray
xg = np.load("xr_global_hourly_2023.npy").astype("f8")
xa = np.load("xr_annual_mean_2023.npy")
# Both weight sets sum to 1, so sum((w - w') T) = sum((w - w') (T - c)) for any c: the two means
# differ by at most L1 x max|T - c|, with c the centre of the year's range (the files' own
# minimum_value / maximum_value attributes), plus float64 summation noise.
lo, hi = [], []
for f in sorted(glob.glob("/tmp/era5/*.nc")):
    with netCDF4.Dataset(f) as d:
        lo.append(float(d.variables["VAR_2T"].minimum_value))
        hi.append(float(d.variables["VAR_2T"].maximum_value))
half = (max(hi) - min(lo)) / 2
rec("era5_range_K", "%.2f..%.2f" % (min(lo), max(hi)))
tol = L1 * half + FLD * float(np.max(np.abs(g)))
d = float(np.max(np.abs(g - xg)))
rec("xarray_hourly_global_tol_K", "%.3e" % tol)
bound("cdo_vs_xarray_hourly_global_max_abs_K", d, tol)
r, same = half_ulp32(ann, xa.reshape(721, 1440))
rec("cdo_vs_xarray_annual_float32_identical", same)
bound("cdo_vs_xarray_annual_max_diff_in_half_ulp32", r, 1.0 + 1e-6, "%.4f")

# ------------------------------------------------------------------ 8. NCO's weighted mean
for x, field in (("ann", ann_n.reshape(1, 721, 1440)), ("mon", mon_n)):
    ng = var("%s_nco_g.nc" % x).reshape(-1)
    npg = (field.astype("f8") * area).sum(axis=(1, 2)) / area.sum()
    r, same = half_ulp32(npg, ng)
    allow = 1.0 + FLD * float(np.max(npg)) / (0.5 * float(np.spacing(np.float32(np.max(npg)))))
    rec("nco_ncwa_vs_numpy_%s_float32_identical" % x, "%d of %d" % (same, ng.size))
    bound("nco_ncwa_vs_numpy_%s_max_diff_in_half_ulp32" % x, r, allow, "%.4f")

# ------------------------------------------------------------------ 9. the published numbers
C = 273.15
mon_g = var("mon_g.nc").reshape(-1)
k = int(np.argmax(day_g))
rec("global_mean_2023_C", "%.4f" % (ann_g - C))
rec("global_mean_2023_nco_C", "%.4f" % (float(var("ann_nco_g.nc").reshape(-1)[0]) - C))
rec("global_monthly_C", ",".join("%.3f" % (v - C) for v in mon_g))
rec("daily_global_max_C", "%.4f day %d" % (day_g[k] - C, k + 1))
for name, got, want in (("published_2023_annual", ann_g - C, 14.98),
                        ("published_july_2023", mon_g[6] - C, 16.95),
                        ("published_daily_record", day_g[k] - C, 17.08)):
    rec(name + "_diff_C", "%+.4f" % (got - want))
    if not bool(abs(got - want) <= 0.01):
        die("%s: %.4f C vs C3S's %.2f C" % (name, got, want))
if k + 1 not in (186, 187, 188):          # 5, 6, 7 July 2023
    die("the record day is day %d of 2023; C3S put it on 6 July (day 187)" % (k + 1))

# ------------------------------------------------------------------ 10. the default spellings agree
gj = var("default_g_jan.nc").reshape(-1)
fj = var("fast_g_jan.nc").reshape(-1)
bound("fldmean_default_vs_setgridarea_jan_rel", rel(gj, fj), FLD)
bound("fldmean_setgridarea_jan_vs_year_series_rel", rel(fj, g[:744]), FLD)
rec("ncra_default_vs_cnk_csh_jan_bit_identical",
    str(bool(np.array_equal(var("default_ncra_jan.nc"), var("mon_nco_01.nc")))))
if not bool(np.array_equal(var("default_ncra_jan.nc"), var("mon_nco_01.nc"))):
    die("ncra's January differs between the default chunk cache and --cnk_csh")

# ------------------------------------------------------------------ hashes, for the cross-chip comparison
for name, a in (("g", g), ("gr", gr), ("ann", ann), ("mon", mon), ("day", day), ("dhour", dh),
                ("ann_nco", ann_n), ("mon_nco", mon_n), ("day_nco", day_n), ("area", area), ("anom", an)):
    rec("hash_" + name, h(a))
np.save("ann_cdo.npy", ann)
dump()
print("CDO NCO REAL OK", flush=True)
