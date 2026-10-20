"""The netCDF side of the cross-code check: read GFDL-CM4's 9 ESGF netCDF files with netCDF4 (the
`climate` env -- no zarr, no dask in the loop) and write what the Zarr run must reproduce exactly:

  * sha256 of the whole tas array as raw little-endian float32, C order, time-major (12.49 GB);
  * every (day, latitude-row) sum of tas * 2**24 as an exact int64 -- every float32 >= 0.5 is an
    integer at that scale, so these sums are exact and order-free (asserted, not assumed);
  * the coordinates (time, time_bnds, lat, lat_bnds, lon, lon_bnds) as raw float64.

    python3 -u ncref.py <nc_dir> <out_dir>
"""
import glob
import hashlib
import json
import os
import resource
import sys
import time

import netCDF4
import numpy as np

NC, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
SCALE = 2.0 ** 24
ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok = bool(ok)
    ok_all &= ok
    print("%-4s %s %s" % ("ok" if ok else "FAIL", name, detail), flush=True)


files = sorted(glob.glob(os.path.join(NC, "*.nc")))
check("9 netCDF files", len(files) == 9, str(len(files)))
print("netCDF4 %s, libnetcdf %s, hdf5 %s, numpy %s" % (
    getattr(netCDF4, "__version__", "?"), getattr(netCDF4, "__netcdf4libversion__", "?"),
    getattr(netCDF4, "__hdf5libversion__", "?"), np.__version__), flush=True)

h = hashlib.sha256()
rows, times, tbnds = [], [], []
coords0 = None
info = []
nmin, nmax = np.inf, -np.inf
T0 = time.time()
for f in files:
    ds = netCDF4.Dataset(f)
    v = ds["tas"]
    v.set_auto_maskandscale(False)
    ds["time"].set_auto_maskandscale(False)
    ds["time_bnds"].set_auto_maskandscale(False)
    co = {}
    for c in ("lat", "lat_bnds", "lon", "lon_bnds"):
        ds[c].set_auto_maskandscale(False)
        co[c] = np.ascontiguousarray(ds[c][:], dtype="<f8")
    if coords0 is None:
        coords0 = co
        units = {"time": ds["time"].units, "calendar": ds["time"].calendar}
    else:
        check("coords identical in " + os.path.basename(f),
              all(np.array_equal(co[c], coords0[c]) for c in co))
    filt = v.filters()
    info.append({"file": os.path.basename(f), "shape": list(v.shape), "dtype": str(v.dtype),
                 "chunking": v.chunking(), "zlib": filt.get("zlib"), "shuffle": filt.get("shuffle"),
                 "complevel": filt.get("complevel")})
    times.append(np.asarray(ds["time"][:], dtype="<f8"))
    tbnds.append(np.asarray(ds["time_bnds"][:], dtype="<f8"))
    n = v.shape[0]
    for t0 in range(0, n, 365):
        x = np.ascontiguousarray(v[t0:t0 + 365], dtype="<f4")
        h.update(memoryview(x).cast("B"))
        y = x.astype(np.float64) * SCALE
        yi = y.astype(np.int64)
        if not np.array_equal(yi.astype(np.float64), y):
            check("every value an integer at 2**24", False, f)
            sys.exit(1)
        rows.append(yi.sum(axis=2))
        nmin, nmax = min(nmin, float(x.min())), max(nmax, float(x.max()))
    ds.close()
    print("read %s  %d days  %.0f s" % (os.path.basename(f), n, time.time() - T0), flush=True)
wall = time.time() - T0

R = np.concatenate(rows)
t = np.concatenate(times)
tb = np.concatenate(tbnds)
check("60225 days", R.shape == (60225, 180), str(R.shape))
check("every value an integer at 2**24 (so the row sums are exact)", True,
      "min %.4f K max %.4f K" % (nmin, nmax))
check("time strictly daily", np.array_equal(np.diff(t), np.ones(t.size - 1)), "")
np.save(os.path.join(OUT, "rows.npy"), R)
for name, a in (("time", t), ("time_bnds", tb), *coords0.items()):
    np.save(os.path.join(OUT, name + ".npy"), a)

ref = {"tas_sha256": h.hexdigest(), "shape": [int(R.shape[0]), 180, 288],
       "rows_sha256": hashlib.sha256(R.tobytes()).hexdigest(),
       "total_scaled": str(int(R.sum(dtype=object))), "min": nmin, "max": nmax,
       "time_units": units, "files": info, "read_wall_s": round(wall, 1),
       "ru_maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
json.dump(ref, open(os.path.join(OUT, "ref.json"), "w"), indent=1)
print(json.dumps({k: ref[k] for k in ("tas_sha256", "rows_sha256", "total_scaled", "read_wall_s")}))
if not ok_all:
    sys.exit(1)
print("NCREF OK")
