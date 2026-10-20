"""datashader-real: every NYC yellow-taxi trip of 2009-2010, pickups and dropoffs, rasterised.

    python3 -u real.py <dir of the 24 parquet files> <out dir> <threads>

The workload is what a datashader user does with this data: dask.read_parquet over the whole
dataset, persist, then Canvas.points on a 4096 x 4096 NYC canvas -- pickup count, passengers and
fare (as integer cents) summed per cell, a mean, and the dropoff count; then two coarser canvases.

The checks are integer identities, so none has a tolerance:
  * the canvas extent is dyadic (0.5 deg spans, a multiple of 2^-13 at each end, 2^12 cells), so a
    point's bin is exact arithmetic in datashader AND in numpy -- the comparison cannot be decided
    by a rounding at a bin edge;
  * every raster == np.histogram2d of the same points, cell for cell (transposed: datashader is y,x);
  * raster totals == numpy's boolean inside-count (conservation), and rows read == parquet footers;
  * the dask (partitioned, persisted) rasters == the sum of 2M-row pandas rasters (partitioning);
  * the 1024- and 2048-wide rasters == the 4096-wide one block-summed (the edges nest exactly);
  * mean == sum / count in every cell, and NaN exactly where count == 0.
"""
import gc
import glob
import json
import os
import sys
import time

import numpy as np

W = 4096
X0, X1, Y0, Y1 = -74.25, -73.75, 40.5, 41.0
COLS = {
    2009: dict(plon="Start_Lon", plat="Start_Lat", dlon="End_Lon", dlat="End_Lat",
               pax="Passenger_Count", fare="Fare_Amt"),
    2010: dict(plon="pickup_longitude", plat="pickup_latitude", dlon="dropoff_longitude",
               dlat="dropoff_latitude", pax="passenger_count", fare="fare_amount"),
}
EXPECT_FILES = int(os.environ.get("EXPECT_FILES", "24"))


def year_of(path):
    return int(os.path.basename(path).split("_")[2][:4])


def load(path, rows=2_000_000):
    """One file in batches of float64 numpy columns, the same values the dask frame holds."""
    import pyarrow.parquet as pq
    c = COLS[year_of(path)]
    for t in pq.ParquetFile(path).iter_batches(batch_size=rows, columns=list(c.values())):
        d = {k: t.column(v).to_numpy(zero_copy_only=False).astype("float64") for k, v in c.items()}
        d["cents"] = np.round(d.pop("fare") * 100)
        yield d


def ref_worker(paths):
    """Independent reference for a share of the files: numpy only, plus pandas datashader per batch."""
    import pandas as pd
    import datashader as ds
    rng = [[X0, X1], [Y0, Y1]]
    cvs = ds.Canvas(plot_width=W, plot_height=W, x_range=(X0, X1), y_range=(Y0, Y1))
    acc = {k: np.zeros((W, W), dtype) for k, dtype in
           (("n_pu", np.int64), ("n_do", np.int64), ("pax_pu", np.float64), ("cents_pu", np.float64),
            ("ds_pu", np.int64))}
    sc = dict(rows=0, nan_pu=0, nan_do=0, in_pu=0, in_do=0, pax_in=0.0, cents_in=0.0,
              nan_pax=0, nan_cents=0, cents_n_in=0)
    t_np = t_ds = 0.0
    for d in (d for p in paths for d in load(p)):
        sc["rows"] += len(d["plon"])
        # datashader's sum/count(column) skip NaN values; the reference does the same, explicitly
        for k in ("pax", "cents"):
            bad = np.isnan(d[k])
            sc["nan_" + k] += int(bad.sum())
            d[k + "_ok"] = ~bad
            d[k] = np.where(bad, 0.0, d[k])
        t0 = time.time()
        for side in ("pu", "do"):
            x, y = (d["plon"], d["plat"]) if side == "pu" else (d["dlon"], d["dlat"])
            fin = np.isfinite(x) & np.isfinite(y)
            sc["nan_" + side] += int((~fin).sum())
            inside = fin & (x >= X0) & (x <= X1) & (y >= Y0) & (y <= Y1)
            sc["in_" + side] += int(inside.sum())
            xf, yf = x[fin], y[fin]
            acc["n_" + side] += np.histogram2d(xf, yf, bins=W, range=rng)[0].astype(np.int64)
            if side == "pu":
                sc["pax_in"] += float(d["pax"][inside].sum())
                sc["cents_in"] += float(d["cents"][inside].sum())
                sc["cents_n_in"] += int((inside & d["cents_ok"]).sum())
                acc["pax_pu"] += np.histogram2d(xf, yf, bins=W, range=rng, weights=d["pax"][fin])[0]
                acc["cents_pu"] += np.histogram2d(xf, yf, bins=W, range=rng, weights=d["cents"][fin])[0]
        t1 = time.time()
        df = pd.DataFrame({"plon": d["plon"], "plat": d["plat"]})
        acc["ds_pu"] += np.asarray(cvs.points(df, "plon", "plat", ds.count())).astype(np.int64)
        t_ds += time.time() - t1
        t_np += t1 - t0
        del d, df
        gc.collect()
    sc["t_numpy"] = t_np
    sc["t_ds_pandas"] = t_ds
    sc["maxrss_kib"] = vmhwm_kib()
    return acc, sc


def vmhwm_kib():
    """This process's peak RSS. Not ru_maxrss: Linux carries that across exec, so a spawned
    worker would report its parent's peak."""
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    return -1


def main():
    data, out, nt = sys.argv[1], sys.argv[2], int(sys.argv[3])
    os.makedirs(out, exist_ok=True)
    res, fails = {}, []
    logf = open(os.path.join(out, "check.txt"), "w")

    def rec(k, v):
        res[k] = v
        line = "%-44s %s" % (str(k), str(v))
        print(line, flush=True)
        logf.write(line + "\n"); logf.flush()

    def check(name, ok, detail):
        ok = bool(ok)
        rec(("PASS " if ok else "FAIL ") + name, detail)
        if not ok:
            fails.append(name)

    import numba, pandas, dask, pyarrow
    import datashader as ds
    import dask.dataframe as dd
    for m in (ds, numba, pandas, dask, pyarrow, np):
        rec("version " + m.__name__, getattr(m, "__version__", "unknown"))
    rec("numba threads", getattr(getattr(numba, "config", None), "NUMBA_NUM_THREADS", "unknown"))
    try:
        from threadpoolctl import threadpool_info
        for i in threadpool_info():
            rec("threadpool " + str(i.get("internal_api")), "%s %s threads=%s" % (i.get("version"), i.get("architecture"), i.get("num_threads")))
    except Exception as e:
        rec("threadpool", "unavailable: %r" % (e,))

    files = sorted(glob.glob(os.path.join(data, "yellow_tripdata_*.parquet")))
    rec("files", len(files))
    check("all files present", len(files) == EXPECT_FILES, "%d of %d" % (len(files), EXPECT_FILES))
    import pyarrow.parquet as pq
    rows_footer = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
    rec("rows (parquet footers)", rows_footer)
    rec("points (pickups + dropoffs)", 2 * rows_footer)

    # ---------------- the workload: dask over the whole dataset ----------------
    dask.config.set(scheduler="threads", num_workers=nt)
    cvs = ds.Canvas(plot_width=W, plot_height=W, x_range=(X0, X1), y_range=(Y0, Y1))
    T = {}
    t0 = time.time()
    parts = []
    for year in (2009, 2010):
        fy = [f for f in files if year_of(f) == year]
        if not fy:
            continue
        c = COLS[year]
        f = dd.read_parquet(fy, columns=list(c.values())).rename(columns={v: k for k, v in c.items()})
        parts.append(f)
    ddf = dd.concat(parts) if len(parts) > 1 else parts[0]
    ddf = ddf.assign(pax=ddf.pax.astype("float64"), cents=(ddf.fare * 100).round())
    ddf = ddf[["plon", "plat", "dlon", "dlat", "pax", "cents"]].persist()
    rows_dask = int(ddf.map_partitions(len).compute().sum())
    T["read+persist"] = time.time() - t0
    rec("dask partitions", ddf.npartitions)
    check("rows read == parquet footers", rows_dask == rows_footer, "%d vs %d" % (rows_dask, rows_footer))

    t0 = time.time()
    pu = cvs.points(ddf, "plon", "plat", agg=ds.summary(
        n=ds.count(), pax=ds.sum("pax"), cents=ds.sum("cents"), n_cents=ds.count("cents"),
        mean=ds.mean("cents")))
    T["pickups summary (cold, incl. JIT)"] = time.time() - t0
    t0 = time.time()
    do = cvs.points(ddf, "dlon", "dlat", ds.count())
    T["dropoffs count"] = time.time() - t0
    t0 = time.time()
    pu2 = cvs.points(ddf, "plon", "plat", ds.count())
    T["pickups count (warm)"] = time.time() - t0
    coarse = {}
    for w in (2048, 1024):
        t0 = time.time()
        cc = ds.Canvas(plot_width=w, plot_height=w, x_range=(X0, X1), y_range=(Y0, Y1))
        coarse[w] = np.asarray(cc.points(ddf, "plon", "plat", ds.count())).astype(np.int64)
        T["pickups count %d" % w] = time.time() - t0
    T["datashader total"] = sum(v for k, v in T.items() if k != "read+persist")
    for k, v in T.items():
        rec("time " + k + " s", "%.2f" % v)
    rec("TOOL_WALL_S", "%.2f" % (T["read+persist"] + T["datashader total"]))
    rec("datashader rate (warm pickups count), points/s", "%.3e" % (rows_footer / T["pickups count (warm)"]))

    # datashader names the dims after the columns, rows = the y column, and y ascends
    check("raster dims (y column, x column)", tuple(pu.n.dims) == ("plat", "plon") and pu.n.shape == (W, W), str(pu.n.dims))
    yc, xc = pu.n.coords["plat"].values, pu.n.coords["plon"].values
    check("y ascending, cell centres on the dyadic grid", bool(np.all(np.diff(yc) > 0)) and yc[0] == Y0 + 0.5 * (Y1 - Y0) / W
          and xc[0] == X0 + 0.5 * (X1 - X0) / W, "y[0]=%r x[0]=%r" % (float(yc[0]), float(xc[0])))
    n_pu = np.asarray(pu.n).astype(np.int64)
    pax_pu = np.asarray(pu.pax, dtype=np.float64)
    cents_pu = np.asarray(pu.cents, dtype=np.float64)
    mean_pu = np.asarray(pu["mean"], dtype=np.float64)
    nc_pu = np.asarray(pu.n_cents).astype(np.int64)
    # a datashader sum over no points is NaN, not 0 (as with mean): assert where, then compare as 0
    for name, a_, n_ in (("sum('pax')", pax_pu, n_pu), ("sum('cents')", cents_pu, nc_pu)):
        check(name + " is NaN exactly where its count == 0", bool(np.array_equal(np.isnan(a_), n_ == 0)), int(np.isnan(a_).sum()))
    pax_pu = np.nan_to_num(pax_pu, nan=0.0)
    cents_pu = np.nan_to_num(cents_pu, nan=0.0)
    n_do = np.asarray(do).astype(np.int64)
    n_pu2 = np.asarray(pu2).astype(np.int64)
    del ddf, pu, do, pu2, parts
    gc.collect()

    # ---------------- the reference: numpy histogram2d, per-file pandas datashader ----------------
    os.environ.update(NUMBA_NUM_THREADS="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    big_first = sorted(files, key=os.path.getsize, reverse=True)
    shares = [big_first[i::nt] for i in range(nt)]
    t0 = time.time()
    with ProcessPoolExecutor(nt, mp_context=mp.get_context("spawn")) as ex:
        got = list(ex.map(ref_worker, [s for s in shares if s]))
    T_ref = time.time() - t0
    R = {k: sum(g[0][k] for g in got) for k in got[0][0]}
    S = {k: sum(g[1][k] for g in got) for k in got[0][1] if k != "maxrss_kib"}
    rec("time reference (numpy + per-batch pandas) s", "%.2f" % T_ref)
    rec("reference worker peak RSS GiB", "%.2f" % (max(g[1]["maxrss_kib"] for g in got) / 2**20))

    check("reference rows == footers", S["rows"] == rows_footer, S["rows"])
    rec("pickups inside extent", S["in_pu"])
    rec("dropoffs inside extent", S["in_do"])
    rec("pickups non-finite", S["nan_pu"])
    rec("passenger_count NaN / fare NaN", "%d / %d" % (S["nan_pax"], S["nan_cents"]))
    check("count('cents') total == numpy inside non-NaN fares", int(nc_pu.sum()) == S["cents_n_in"], "%d vs %d" % (nc_pu.sum(), S["cents_n_in"]))
    rec("dropoffs non-finite", S["nan_do"])
    rec("pickups outside extent (finite)", rows_footer - S["nan_pu"] - S["in_pu"])
    check("pickups: raster total == numpy inside-count", int(n_pu.sum()) == S["in_pu"], "%d vs %d" % (n_pu.sum(), S["in_pu"]))
    check("dropoffs: raster total == numpy inside-count", int(n_do.sum()) == S["in_do"], "%d vs %d" % (n_do.sum(), S["in_do"]))
    check("passengers: raster total == numpy inside-sum", pax_pu.sum() == S["pax_in"], "%r vs %r" % (pax_pu.sum(), S["pax_in"]))
    check("fare cents: raster total == numpy inside-sum", cents_pu.sum() == S["cents_in"], "%r vs %r" % (cents_pu.sum(), S["cents_in"]))
    check("cents sums below 2^53 (exact)", np.abs(cents_pu).sum() < 2.0**53, "%.3e" % np.abs(cents_pu).sum())
    for name, a, h in (("pickup count", n_pu, R["n_pu"]), ("dropoff count", n_do, R["n_do"]),
                       ("pickup passengers", pax_pu, R["pax_pu"]), ("pickup fare cents", cents_pu, R["cents_pu"])):
        nd = int((a != h.T).sum())
        check(name + " == histogram2d.T, every cell", nd == 0, "%d of %d cells differ" % (nd, W * W))
    nd = int((n_pu != R["n_pu"]).sum())
    check("control: pickup count without the transpose differs", nd > 0, "%d cells differ" % nd)
    nd = int((n_pu != R["ds_pu"]).sum())
    check("dask persisted == sum of 2M-row pandas rasters", nd == 0, "%d cells differ" % nd)
    check("pickup count warm repeat == summary count", bool(np.array_equal(n_pu, n_pu2)), "bit-identical")
    for w in (2048, 1024):
        f = W // w
        blk = n_pu.reshape(w, f, w, f).sum(axis=(1, 3))
        nd = int((coarse[w] != blk).sum())
        check("%d-wide raster == 4096 block-summed %dx%d" % (w, f, f), nd == 0, "%d cells differ" % nd)
    rec("empty pickup cells", int((n_pu == 0).sum()))
    empty = nc_pu == 0
    check("mean is NaN exactly where count('cents') == 0", bool(np.array_equal(np.isnan(mean_pu), empty)), int(np.isnan(mean_pu).sum()))
    with np.errstate(invalid="ignore", divide="ignore"):
        q = cents_pu / nc_pu
    dm = np.abs(mean_pu[~empty] - q[~empty]).max()
    check("mean == sum / count exactly", dm == 0, "max |diff| %.3e" % dm)

    iy, ix = np.unravel_index(np.argmax(n_pu), n_pu.shape)
    step = (X1 - X0) / W
    rec("busiest pickup cell (lon, lat, trips)", "%.5f %.5f %d" % (X0 + (ix + 0.5) * step, Y0 + (iy + 0.5) * step, n_pu[iy, ix]))
    rec("non-empty pickup / dropoff cells", "%d / %d" % ((n_pu > 0).sum(), (n_do > 0).sum()))
    rec("mean fare over inside pickups $", "%.6f" % (S["cents_in"] / S["cents_n_in"] / 100))

    import hashlib
    for name, a in (("n_pu", n_pu), ("n_do", n_do), ("pax_pu", pax_pu), ("cents_pu", cents_pu)):
        rec("sha256 " + name, hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest())
    np.savez_compressed(os.path.join(out, "rasters.npz"), n_pu=n_pu.astype(np.uint32), n_do=n_do.astype(np.uint32))
    rec("peak RSS main process GiB", "%.2f" % (vmhwm_kib() / 2**20))
    json.dump({k: (v if isinstance(v, (int, float, str)) else str(v)) for k, v in res.items()},
              open(os.path.join(out, "results.json"), "w"), indent=1)
    if fails:
        rec("FAILED", ", ".join(fails))
        sys.exit(1)
    rec("DATASHADER REAL OK", "")


if __name__ == "__main__":
    main()
