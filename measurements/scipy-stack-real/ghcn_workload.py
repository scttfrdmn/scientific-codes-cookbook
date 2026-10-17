"""NumPy / SciPy / pandas on one complete year of GHCN-Daily (RODA), every result checked by an identity.

    python3 -u ghcn_workload.py <year.csv> <ghcnd-stations.txt> <label>

pandas: parse 38M station-day rows (pyarrow engine), QC partition, groupby, merge, resample.
NumPy:  the same integer aggregates by an independent route (factorize + bincount).
SciPy:  grid the year's station-mean TMAX onto a global 0.25 deg grid (1,036,800 unknowns) by
        solving (W + alpha*L) x = b, with SuperLU and with CG, against a RIGOROUS condition bound.
Every assertion is an identity or a theorem, not a band. Writes result-<label>.txt and digest-<label>.txt.
"""
import ctypes
import hashlib
import os
import resource
import sys
import time

import numpy as np
import pandas as pd
import scipy
import scipy.sparse as sp
import scipy.sparse.linalg as spla
import threadpoolctl

CSV, STATIONS, LBL = sys.argv[1], sys.argv[2], sys.argv[3]
OUT, DIG = [], []
FAILS = []


def rec(k, v):
    line = "%s\t%s" % (str(k), str(v))   # str() every value: a version tuple must never become format args
    OUT.append(line)
    print(line, flush=True)


def check(name, ok, detail=""):
    rec("CHECK " + name, ("PASS " if ok else "FAIL ") + str(detail))
    if not ok:
        FAILS.append(name)


def digest(name, frame):
    h = hashlib.sha256(frame.to_csv(index=True, lineterminator="\n").encode()).hexdigest()
    DIG.append("%s\t%s" % (name, h))
    rec("sha256 " + name, h)


T = {}


def tick(k, t0):
    T[k] = time.perf_counter() - t0
    rec("time_s " + k, "%.2f" % T[k])


rec("label", LBL)
for m in (np, scipy, pd):
    rec("version " + m.__name__, getattr(m, "__version__", "unknown"))
try:
    import pyarrow
    rec("version pyarrow", getattr(pyarrow, "__version__", "unknown"))
except Exception as e:
    rec("version pyarrow", "unavailable " + type(e).__name__)
for i in threadpoolctl.threadpool_info():
    if i.get("internal_api") == "openblas":
        try:
            lib = ctypes.CDLL(str(i.get("filepath")))
            lib.openblas_get_corename.restype = ctypes.c_char_p
            rec("openblas_corename", lib.openblas_get_corename().decode())
        except Exception as e:
            rec("openblas_corename", "unavailable " + type(e).__name__)
        rec("openblas", "%s threads=%s" % (i.get("version"), i.get("num_threads")))

T0 = time.perf_counter()

# ---------------------------------------------------------------- read
t = time.perf_counter()
with open(CSV, "rb") as fh:
    nl = 0
    while True:
        b = fh.read(1 << 24)
        if not b:
            break
        nl += b.count(b"\n")
rows_in_file = nl - 1                       # header line
tick("count_lines", t)

t = time.perf_counter()
# The five columns the workload uses; M_FLAG, S_FLAG and OBS_TIME are not read.
df = pd.read_csv(CSV, engine="pyarrow", usecols=["ID", "DATE", "ELEMENT", "DATA_VALUE", "Q_FLAG"],
                 dtype={"ID": "category", "ELEMENT": "category", "DATE": "int32", "DATA_VALUE": "int64",
                        "Q_FLAG": "str"})
tick("read_csv", t)
N = len(df)
rec("rows", N)
check("rows parsed == data lines in the file", N == rows_in_file, "%d vs %d" % (N, rows_in_file))
rec("stations_in_year", df["ID"].nunique())
rec("elements", df["ELEMENT"].nunique())

# GHCN guarantees one value per station, day and element.
t = time.perf_counter()
dups = int(df.duplicated(subset=["ID", "DATE", "ELEMENT"]).sum())
tick("duplicate_scan", t)
check("one value per (station, date, element)", dups == 0, "%d duplicates" % dups)

# ---------------------------------------------------------------- QC partition
qc_bad = df["Q_FLAG"].notna() & (df["Q_FLAG"] != "")
nbad = int(qc_bad.sum())
good = df[~qc_bad]
rec("rows_qc_flagged", nbad)
check("QC partition: kept + flagged == all", len(good) + nbad == N, "%d + %d" % (len(good), nbad))
TOTAL = int(df["DATA_VALUE"].sum())
check("value sum partitions under QC", int(good["DATA_VALUE"].sum()) + int(df.loc[qc_bad, "DATA_VALUE"].sum()) == TOTAL,
      "total %d" % TOTAL)

# ---------------------------------------------------------------- groupby, and the same sums by NumPy
t = time.perf_counter()
g = df.groupby(["ID", "ELEMENT"], observed=True)["DATA_VALUE"].agg(["count", "sum"])
tick("groupby_station_element", t)
rec("groups_station_element", len(g))
check("groupby conservation: sum of group sums == column sum (int64, exact)", int(g["sum"].sum()) == TOTAL)
check("groupby conservation: sum of group counts == rows", int(g["count"].sum()) == N)

t = time.perf_counter()
key = df["ID"].cat.codes.to_numpy().astype(np.int64) * len(df["ELEMENT"].cat.categories) + df["ELEMENT"].cat.codes.to_numpy()
codes, uniq = pd.factorize(key, sort=True)
nb_sum = np.bincount(codes, weights=None, minlength=len(uniq))   # counts
vals = df["DATA_VALUE"].to_numpy()
order = np.argsort(codes, kind="stable")
bounds = np.r_[0, np.cumsum(nb_sum)]
nb_val = np.add.reduceat(vals[order], bounds[:-1]) if len(uniq) else np.array([], dtype=np.int64)
tick("numpy_route", t)
check("NumPy route == pandas groupby, every group (counts and int64 sums)",
      len(uniq) == len(g) and np.array_equal(nb_sum, g["count"].to_numpy()) and np.array_equal(nb_val, g["sum"].to_numpy()),
      "%d groups" % len(uniq))
digest("groupby_station_element", g)

t = time.perf_counter()
dates = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d")
tick("parse_dates", t)
gm = df.groupby([dates.dt.month.rename("month"), "ELEMENT"], observed=True)["DATA_VALUE"].agg(["count", "sum"])
check("groupby conservation by (month, element)", int(gm["sum"].sum()) == TOTAL and int(gm["count"].sum()) == N)
check("every date lies in the file's year", int(dates.dt.year.nunique()) == 1, str(sorted(dates.dt.year.unique())[:3]))
digest("groupby_month_element", gm)

# ---------------------------------------------------------------- merge with the station table
t = time.perf_counter()
st = pd.read_fwf(STATIONS, colspecs=[(0, 11), (12, 20), (21, 30), (31, 37), (41, 71)],
                 names=["ID", "LAT", "LON", "ELEV", "NAME"], dtype={"ID": "str"})
tick("read_stations", t)
rec("stations_in_table", len(st))
check("station table keys are unique", st["ID"].is_unique)
t = time.perf_counter()
ids = df["ID"].astype("str")
m = pd.merge(pd.DataFrame({"ID": ids, "ELEMENT": df["ELEMENT"], "DATA_VALUE": df["DATA_VALUE"]}),
             st[["ID", "LAT", "LON"]], on="ID", how="left", validate="m:1", indicator=True)
tick("merge_left_m1", t)
matched = int((m["_merge"] == "both").sum())
expect = int(ids.isin(set(st["ID"])).sum())
check("merge cardinality: left m:1 keeps every row", len(m) == N, "%d rows" % len(m))
check("merge cardinality: matched == rows whose ID is in the table (independent isin)", matched == expect,
      "%d matched, %d unmatched" % (matched, N - matched))
mi = pd.merge(m[["ID", "DATA_VALUE"]], st[["ID"]], on="ID", how="inner")
check("merge cardinality: inner == matched", len(mi) == matched)
cc = m.groupby(m["ID"].str.slice(0, 2), observed=True)["DATA_VALUE"].sum()
check("groupby conservation by country after merge", int(cc.sum()) == TOTAL, "%d countries" % len(cc))
digest("sum_by_country", cc)
del m, mi

# ---------------------------------------------------------------- resample: monthly TMAX climatology per station
t = time.perf_counter()
tx = good[good["ELEMENT"] == "TMAX"]
tx = pd.DataFrame({"ID": tx["ID"].astype("str").to_numpy(), "v": tx["DATA_VALUE"].to_numpy()},
                  index=pd.DatetimeIndex(dates[tx.index].to_numpy(), name="DATE"))
mon = tx.groupby("ID")["v"].resample("MS").agg(["count", "sum"])
mon = mon[mon["count"] > 0]
tick("resample_monthly_tmax", t)
rec("tmax_rows_qc", len(tx))
rec("station_months", len(mon))
check("resample conservation: monthly sums == TMAX sum, monthly counts == TMAX rows",
      int(mon["sum"].sum()) == int(tx["v"].sum()) and int(mon["count"].sum()) == len(tx))
digest("resample_monthly_tmax", mon)

# ---------------------------------------------------------------- SciPy: grid the station means
ann = tx.groupby("ID")["v"].agg(["count", "sum"])
MIN_DAYS = int(os.environ.get("MIN_DAYS", "300"))   # a station with most of the year
ann = ann[ann["count"] >= MIN_DAYS]
ann = ann.join(st.set_index("ID")[["LAT", "LON"]], how="inner")
y = (ann["sum"] / ann["count"]).to_numpy() / 10.0          # annual mean TMAX, deg C
rec("stations_gridded", len(ann))
del df, good, dates, tx, mon, g, gm, ids, qc_bad   # the frames are done; the solve gets the RAM
check("stations to grid", len(ann) > 0 and bool(np.isfinite(y).all()), "%d, all finite" % len(ann))
DEG = float(os.environ.get("GRID_DEG", "0.25"))   # 0.25 deg: 1440 x 720 = 1,036,800 unknowns
NX, NY = int(round(360 / DEG)), int(round(180 / DEG))
rec("grid", "%gdeg %dx%d" % (DEG, NX, NY))
ix = np.clip(np.floor((ann["LON"].to_numpy() + 180.0) / DEG).astype(np.int64), 0, NX - 1)
iy = np.clip(np.floor((ann["LAT"].to_numpy() + 90.0) / DEG).astype(np.int64), 0, NY - 1)
cell = iy * NX + ix
n = NX * NY
EPS, ALPHA = 1e-3, 1.0
cnt = np.bincount(cell, minlength=n).astype(np.float64)
w = cnt + EPS
b = np.bincount(cell, weights=y, minlength=n)
# 5-point graph Laplacian: periodic in longitude, no flux across the poles. PSD, zero row sums.
idx = np.arange(n).reshape(NY, NX)
e_r = (idx, np.roll(idx, -1, axis=1))                     # east neighbour, wraps
e_u = (idx[:-1, :], idx[1:, :])                            # north neighbour, none past the pole
I = np.concatenate([e_r[0].ravel(), e_u[0].ravel()])
J = np.concatenate([e_r[1].ravel(), e_u[1].ravel()])
Adj = sp.coo_matrix((np.ones(len(I)), (I, J)), shape=(n, n))
Adj = (Adj + Adj.T).tocsr()
deg = np.asarray(Adj.sum(axis=1)).ravel()
L = sp.diags(deg) - Adj
A = (sp.diags(w) + ALPHA * L).tocsc()
check("Laplacian rows sum to zero", float(np.abs(np.asarray(L.sum(axis=1))).max()) == 0.0)
rec("unknowns", n)
rec("nnz", A.nnz)
# Rigorous: lambda_min(A) >= min(w) (L is PSD, Weyl); lambda_max(A) <= max(w) + alpha*2*max(deg) (Gershgorin on L).
KAPPA = (w.max() + ALPHA * 2 * deg.max()) / w.min()
rec("kappa2_upper_bound", "%.6e" % KAPPA)

Aq = A.astype(np.longdouble)
rec("longdouble", "%s (eps %.3e)" % (np.dtype(np.longdouble).name, float(np.finfo(np.longdouble).eps)))


def lnorm(v):
    v = v.astype(np.longdouble)
    return np.sqrt((v * v).sum())


def relres(x, rhs):
    """||rhs - A x|| / ||rhs||, in extended precision so the residual is not lost to rounding."""
    r = rhs.astype(np.longdouble) - Aq @ x.astype(np.longdouble)
    return float(lnorm(r) / lnorm(rhs)), r


def fwd_bound(xa, xb):
    """Theorem (A SPD): ||xa-xb||/||xa|| <= kappa2 * ||A(xa-xb)|| / ||A xa||. Both sides, extended precision."""
    dx = xa.astype(np.longdouble) - xb.astype(np.longdouble)
    lhs = float(lnorm(dx) / lnorm(xa))
    rhs = float(KAPPA * lnorm(Aq @ dx) / lnorm(Aq @ xa.astype(np.longdouble)))
    return lhs, rhs


t = time.perf_counter()
lu = spla.splu(A, permc_spec="MMD_AT_PLUS_A")
tick("superlu_factor", t)
rec("lu_fill_nnz", lu.L.nnz + lu.U.nnz)
t = time.perf_counter()
x_lu = lu.solve(b)
tick("superlu_solve", t)
rr_lu, r = relres(x_lu, b)
rec("relres_lu", "%.3e" % rr_lu)
check("SuperLU solution finite, residual finite", bool(np.isfinite(x_lu).all()) and np.isfinite(rr_lu))
# Conservation: 1'L = 0, so sum(w x) == sum(b) exactly in exact arithmetic; computed directly (not through r),
# the gap can only be the residual's, |1'r| <= sqrt(n) ||r|| (Cauchy-Schwarz). A Laplacian with a leaky row breaks it.
dm = abs(float((b.astype(np.longdouble)).sum() - (w.astype(np.longdouble) * x_lu.astype(np.longdouble)).sum()))
mb = float(np.sqrt(n) * lnorm(r))
check("gridding conserves the total: |sum(b) - sum(w x)| <= sqrt(n) ||r||", np.isfinite(dm) and dm <= mb,
      "%.3e vs bound %.3e" % (dm, mb))

t = time.perf_counter()
Dinv = sp.diags(1.0 / A.diagonal())
its = [0]
x_cg, info = spla.cg(A.tocsr(), b, rtol=1e-10, maxiter=20000, M=Dinv, callback=lambda _x: its.__setitem__(0, its[0] + 1))
tick("cg_jacobi", t)
rr_cg, _ = relres(x_cg, b)
rec("cg_info", info)
rec("cg_iterations", its[0])
rec("relres_cg", "%.3e" % rr_cg)
check("CG converged to rtol 1e-10", info == 0 and np.isfinite(rr_cg) and rr_cg <= 1e-10, "%.3e" % rr_cg)
lhs, rhs = fwd_bound(x_lu, x_cg)
check("SuperLU vs CG: |x_lu-x_cg|/|x_lu| <= kappa |A(x_lu-x_cg)|/|A x_lu|", np.isfinite(lhs) and lhs <= rhs,
      "%.3e vs bound %.3e" % (lhs, rhs))
rec("lu_cg_reldiff", "%.3e" % lhs)

# Manufactured solution: the gridded field itself, b* = A x*. Forward error <= kappa * relative residual.
xs = x_lu.copy()
x2 = lu.solve(A @ xs)
fe, fb = fwd_bound(xs, x2)
check("forward error <= kappa * relative residual (known solution)", np.isfinite(fe) and fe <= fb,
      "%.3e vs bound %.3e" % (fe, fb))
rec("forward_error_lu", "%.3e" % fe)
land = cnt > 0
rec("grid_mean_tmax_at_station_cells_C", "%.6f" % float((x_lu[land] * cnt[land]).sum() / cnt[land].sum()))
np.save("grid-%s.npy" % LBL, x_lu.reshape(NY, NX).astype(np.float64))

T["total"] = time.perf_counter() - T0
rec("time_s total_python", "%.2f" % T["total"])
rec("peak_rss_gib_self", "%.2f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576))
rec("checks_failed", len(FAILS))
open("result-%s.txt" % LBL, "w").write("\n".join(OUT) + "\n")
open("digest-%s.txt" % LBL, "w").write("\n".join(DIG) + "\n")
sys.exit(1 if FAILS else 0)
