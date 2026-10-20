"""zarr + dask on a real store: GFDL-CM4 historical daily tas (CMIP6, 60,225 days x 180 x 288,
12.49 GB decoded) as Pangeo published it on AWS Open Data, against the ESGF netCDF it came from.

  1. the Zarr store decodes to exactly the bytes netCDF4 reads from the 9 ESGF files (sha256 of the
     whole array, every exact (day, lat-row) integer sum, every coordinate);
  2. the whole array, rewritten by dask at seven layouts -- v2 and v3, two codecs, time-series
     chunks, a chunk that divides no axis, sharded, and grown by append -- reads back bit-identical,
     region by region on each copy's own grid;
  3. at every layout, integer reductions are exact and equal; float64 sums sit inside a derived
     backward-error bound against the exact value, and a float32 accumulation is the control that
     must miss it; min/max are exact; a dask annual coarsen equals numpy's elementwise.

    python3 -u real.py <threads> <workdir> <ncref_dir> <out_dir>
"""
import fractions
import hashlib
import json
import math
import os
import resource
import shutil
import sys
import time

import numpy as np

NT = int(sys.argv[1])
WORK, NCREF, OUT = sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(OUT, exist_ok=True)
SRC = os.environ.get("ZD_SRC", "/tmp/tas.zarr")
AREA = os.environ.get("ZD_AREA", "/tmp/areacella.zarr")
SCALE = 2 ** 24
EPS = float(np.finfo(np.float64).eps)
OK = True
RESULT = {"checks": [], "layouts": {}, "obs": {}}


def check(name, ok, detail=""):
    global OK
    ok = bool(ok)
    OK &= ok
    detail = detail if isinstance(detail, str) else str(detail)
    print("%-4s %s  %s" % ("ok" if ok else "FAIL", name, detail), flush=True)
    RESULT["checks"].append([name, ok, detail])


def obs(k, v):
    v = v if isinstance(v, (int, float, str, list, dict, bool)) else str(v)
    print("obs  %s  %s" % (k, v), flush=True)
    RESULT["obs"][k] = v


def rss_gib():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20, 2)


def nfiles(p):
    return sum(len(f) for _, _, f in os.walk(p))


def dirsize(p):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs)


def blocks(shape, grid):
    """Every region of `shape` on the regular grid `grid`, ragged at the far edges."""
    import itertools
    rngs = [range(0, n, g) for n, g in zip(shape, grid)]
    for starts in itertools.product(*rngs):
        yield tuple(slice(s, min(s + g, n)) for s, g, n in zip(starts, grid, shape))


import dask
import dask.array as da
import numcodecs
import zarr
from zarr.codecs import BloscCodec, ZstdCodec

dask.config.set(scheduler="threads", num_workers=NT)
print("zarr %s, dask %s, numcodecs %s, numpy %s; dask threads %d, zarr async.concurrency %s" % (
    zarr.__version__, dask.__version__, numcodecs.__version__, np.__version__, NT,
    zarr.config.get("async.concurrency")), flush=True)
T_ALL = time.time()

# ---------------------------------------------------------------- 1. the store as published
g = zarr.open_group(SRC, mode="r")
z = g["tas"]
check("store metadata", z.shape == (60225, 180, 288) and z.chunks == (838, 180, 288)
      and z.dtype == np.float32 and z.metadata.zarr_format == 2,
      "%s chunks %s %s v%d" % (z.shape, z.chunks, z.dtype, z.metadata.zarr_format))
NDAY = z.shape[0]
NCH = -(-NDAY // 838)
chunk_files = sorted(f for f in os.listdir(os.path.join(SRC, "tas")) if not f.startswith("."))
check("72 chunk objects, the last one ragged (60225 = 71 x 838 + 727)",
      chunk_files == sorted("%d.0.0" % i for i in range(NCH)) and NDAY % 838 != 0,
      "%d objects, last %d days" % (len(chunk_files), NDAY - (NCH - 1) * 838))
t0 = time.time()
A = z[...]
t_decode = time.time() - t0
check("decoded array is C-contiguous little-endian float32", A.flags.c_contiguous and A.dtype == "<f4")
obs("decode_whole_store_s", round(t_decode, 1))

ref = json.load(open(os.path.join(NCREF, "ref.json")))
t0 = time.time()
h = hashlib.sha256()
for t in range(0, A.shape[0], 365):
    h.update(memoryview(A[t:t + 365]).cast("B"))
check("Zarr store == ESGF netCDF, sha256 of all 12.49 GB", h.hexdigest() == ref["tas_sha256"],
      h.hexdigest()[:16])

# exact references, from the decoded array
R = np.empty((A.shape[0], 180), dtype=np.int64)
C = np.zeros((180, 288), dtype=np.int64)
amin, amax = np.inf, -np.inf
for t in range(0, A.shape[0], 838):
    y = A[t:t + 838].astype(np.float64) * SCALE
    yi = y.astype(np.int64)
    if not np.array_equal(yi.astype(np.float64), y):
        check("every value an integer at 2**24", False)
        sys.exit(1)
    R[t:t + 838] = yi.sum(axis=2)
    C += yi.sum(axis=0)
    amin, amax = min(amin, float(A[t:t + 838].min())), max(amax, float(A[t:t + 838].max()))
del y, yi
check("every value an integer at 2**24, so integer sums are exact", amin >= 0.5,
      "min %.4f K max %.4f K" % (amin, amax))
nref = np.load(os.path.join(NCREF, "rows.npy"))
check("every (day, lat-row) exact sum == netCDF's, 10.8M of them", np.array_equal(R, nref))
T = int(R.sum(dtype=object))
check("exact total == netCDF's", str(T) == ref["total_scaled"], "%d / 2**24" % T)
check("rows and cells agree on the exact total", int(C.sum(dtype=object)) == T)
D = R.sum(axis=1)                                # exact per-day, < 2**53
# float64 partial sums of one day are exact if every value is a multiple of 2**-16 (true for
# float32 >= 128) and |partial| < 2**37 (2**53 multiples of 2**-16): 51,840 x 512 K < 2**25
EXACT_DAYS = amin >= 128.0 and 51840 * amax < 2.0 ** 37
obs("daily_float64_sums_exact_by_construction", EXACT_DAYS)
N = A.size
ties = int(np.count_nonzero(A == A.max()))
amax_idx = int(np.argmax(A)) if ties == 1 else None
obs("global_max_ties", ties)
truth = fractions.Fraction(T, SCALE)
truth_f = float(truth)                           # correctly rounded

# coordinates against netCDF's
tz = g["time"][...]
tn = np.load(os.path.join(NCREF, "time.npy"))
check("time: zarr int days since 12:00 == netCDF days since 00:00 minus 0.5, all 60225",
      np.array_equal(tz.astype(np.float64) + 0.5, tn) and g["time"].attrs["calendar"] == "noleap",
      "%s .. %s" % (tz[0], tz[-1]))
NY = NDAY // 365
check("noleap: 165 years x 365 days", tz.size == NY * 365 and NY == 165 and np.array_equal(np.diff(tz), np.ones(tz.size - 1)))
tbz = g["time_bnds"][...]
check("time_bnds == netCDF's", np.array_equal(tbz, np.load(os.path.join(NCREF, "time_bnds.npy"))))
for c in ("lat", "lat_bnds", "lon", "lon_bnds"):
    check(c + " == netCDF's", np.array_equal(g[c][...].astype("<f8"), np.load(os.path.join(NCREF, c + ".npy"))))

# cell weights: R^2 dlon (sin phi_n - sin phi_s); dlon is uniform so the lat factor carries it
lb = g["lat_bnds"][...]
lob = g["lon_bnds"][...]
check("lat bounds contiguous, pole to pole", np.array_equal(lb[1:, 0], lb[:-1, 1]) and lb[0, 0] == -90 and lb[-1, 1] == 90)
# the stored bounds are accumulated 1.25s, so widths carry rounding in the last bits: uniform to
# 1e-12 deg makes dlon a constant factor of every cell weight to ~1e-14 relative
dlon = np.diff(lob, axis=1).ravel()
check("lon bounds contiguous, 0..360, uniform 1.25 deg to 1e-12", np.array_equal(lob[1:, 0], lob[:-1, 1])
      and lob[0, 0] == 0 and lob[-1, 1] == 360 and float(np.max(np.abs(dlon - 1.25))) < 1e-12,
      "max |width - 1.25| %.1e deg" % float(np.max(np.abs(dlon - 1.25))))
w = np.sin(np.deg2rad(lb[:, 1])) - np.sin(np.deg2rad(lb[:, 0]))
check("cell weights telescope to sin(90) - sin(-90) = 2", abs(math.fsum(w) - 2.0) < 1e-14,
      "%.3e" % (math.fsum(w) - 2.0))
ga = zarr.open_group(AREA, mode="r")
ac32 = ga["areacella"][...]
ac = ac32.astype(np.float64)
Rearth = 6371.0e3
geo = (Rearth ** 2) * np.deg2rad(1.25) * w[:, None] * np.ones((1, 288))
obs("areacella_sum_over_4piR2_minus_1(R=6371km)", "%.3e" % (math.fsum(ac.ravel()) / (4 * math.pi * Rearth ** 2) - 1))
# areacella is stored as float32, so the published area of every cell must be the spherical area
# R^2 dlon (sin phi_n - sin phi_s), R = 6371 km, rounded once: relative error <= 2**-24
rel = float(np.max(np.abs(ac / geo - 1)))
check("areacella == spherical cell area (R = 6371 km) rounded to float32, all 51,840 cells",
      ac32.dtype == np.float32 and rel <= 2.0 ** -24 * (1 + 1e-9), "max rel %.3e <= 2**-24 = %.3e" % (rel, 2.0 ** -24))

# the exact weighted reference: sum_j w_j * (R_tj / 2**24), each product rounded once, fsum'd
Rf = R.astype(np.float64) / SCALE                # exact: R < 288 * 512 * 2**24 < 2**53
P = Rf * w[None, :]
W_ref = np.array([math.fsum(row) for row in P])
G = W_ref / (288 * math.fsum(w))
yrs = G.reshape(NY, 365).mean(axis=1)
obs("global_mean_tas_1850_1900_K", round(float(yrs[:51].mean()), 4))
obs("global_mean_tas_1995_2014_K", round(float(yrs[145:].mean()), 4))
obs("warming_1995_2014_minus_1850_1900_K", round(float(yrs[145:].mean() - yrs[:51].mean()), 4))
obs("phase1_s", round(time.time() - T_ALL, 1))
obs("rss_after_phase1_gib", rss_gib())

# annual coarsen, exact: dask on the published chunks vs numpy reshape, elementwise
x0 = da.from_zarr(z)
xi0 = (x0.astype(np.float64) * SCALE).astype(np.int64)
Y = da.coarsen(np.sum, xi0.rechunk((3650, 180, 288)), {0: 365}).compute()
Yn = np.empty((NY, 180, 288), dtype=np.int64)
for yr in range(NY):
    Yn[yr] = (A[yr * 365:(yr + 1) * 365].astype(np.float64) * SCALE).astype(np.int64).sum(axis=0)
check("dask annual coarsen == numpy, all 165 x 51,840 cells, exact", np.array_equal(Y, Yn))
check("annual totals conserve every cell's 165-year total, exact", np.array_equal(Y.sum(axis=0), C))
del Y, Yn

# ---------------------------------------------------------------- 2. seven layouts
BL = BloscCodec(cname="lz4", clevel=5, shuffle="shuffle", typesize=4, blocksize=0)
LAYOUTS = [
    # name, zarr_format, chunks, shards, compressors, how
    ("v2-native", 2, (838, 180, 288), None, numcodecs.Blosc(cname="lz4", clevel=5, shuffle=1, blocksize=0), "rewrite"),
    ("v3-native", 3, (838, 180, 288), None, [BL], "rewrite"),
    ("v3-zstd", 3, (838, 180, 288), None, [ZstdCodec(level=3)], "rewrite"),
    ("v3-timeseries", 3, (NDAY, 18, 32), None, [BL], "rewrite"),
    ("v3-ragged", 3, (1000, 37, 41), None, [BL], "rewrite"),
    ("v3-sharded", 3, (365, 45, 72), (3650, 180, 288), [BL], "rewrite"),
    ("v3-append", 3, (838, 180, 288), None, [BL], "append"),
]
rng = np.random.default_rng(20261019)
POINTS = [(int(rng.integers(180)), int(rng.integers(288))) for _ in range(3)]
DAYS = [int(d) for d in rng.integers(NDAY, size=3)]
vals = {}

for name, fmt, chunks, shards, comp, how in LAYOUTS:
    L = {}
    path = os.path.join(WORK, name + ".zarr")
    shutil.rmtree(path, ignore_errors=True)
    kw = dict(store=path, shape=A.shape, chunks=chunks, dtype="<f4", zarr_format=fmt,
              fill_value=np.float32(1.0000000200408773e20), overwrite=True)
    kw["compressors"] = comp
    if shards:
        kw["shards"] = shards
    t0 = time.time()
    if how == "append":
        # grow along time the way a model writes it: one ESGF file's worth at a time
        kw["shape"] = (0, 180, 288)
        zc = zarr.create_array(**kw)
        edges = np.cumsum([0] + [fi["shape"][0] for fi in ref["files"]])
        for a, b in zip(edges[:-1], edges[1:]):
            zc.append(A[a:b], axis=0)
        L["appends"] = len(edges) - 1
    else:
        zc = zarr.create_array(**kw)
        wgrid = shards or chunks
        da.store(da.from_zarr(z).rechunk(wgrid), zc, lock=False)
    L["write_s"] = round(time.time() - t0, 1)
    L["files"] = nfiles(path)
    L["bytes"] = dirsize(path)
    zr = zarr.open_array(path, mode="r")
    grid = shards or chunks
    nchunks = math.prod(-(-n // c) for n, c in zip(A.shape, grid))
    if fmt == 3:
        datafiles = nfiles(os.path.join(path, "c")) if os.path.isdir(os.path.join(path, "c")) else 0
    else:
        datafiles = sum(1 for f in os.listdir(path) if not f.startswith("."))
    check("%s: %d %s on disk" % (name, nchunks, "shards" if shards else "chunks"), datafiles == nchunks,
          "%d data objects, %.2f GB" % (datafiles, L["bytes"] / 1e9))
    # bit identity, region by region on the copy's own grid
    t0 = time.time()
    nreg = bad = 0
    for sl in blocks(A.shape, grid):
        b = zr[sl]
        nreg += 1
        if not np.array_equal(b.view(np.uint32), A[sl].view(np.uint32)):
            bad += 1
    L["verify_s"] = round(time.time() - t0, 1)
    check("%s: bit-identical to the published store" % name, bad == 0, "%d regions" % nreg)
    if name == "v2-native":
        same = sum(open(os.path.join(SRC, "tas", f), "rb").read() == open(os.path.join(path, f), "rb").read()
                   for f in chunk_files)
        L["chunk_objects_byte_identical_to_published"] = same
    if name == "v3-native":
        same = sum(open(os.path.join(WORK, "v2-native.zarr", f), "rb").read()
                   == open(os.path.join(path, "c", *f.split(".")), "rb").read() for f in chunk_files)
        L["chunk_objects_byte_identical_to_v2_rewrite"] = same
    # reads a researcher actually does: one point's whole history, one day's map
    t0 = time.time()
    pts_ok = all(np.array_equal(zr[:, j, i], A[:, j, i]) for j, i in POINTS)
    L["point_series_s"] = round((time.time() - t0) / len(POINTS), 3)
    t0 = time.time()
    maps_ok = all(np.array_equal(zr[d], A[d]) for d in DAYS)
    L["day_map_s"] = round((time.time() - t0) / len(DAYS), 3)
    check("%s: point time series and day maps == numpy" % name, pts_ok and maps_ok)

    # 3. reductions, one dask pass over the copy at its own chunks
    x = da.from_zarr(zr)
    x64 = x.astype(np.float64)
    t0 = time.time()
    rows, tot64, day64, tot32, wsum, mn, mx, am = dask.compute(
        (x64 * SCALE).astype(np.int64).sum(axis=2), x64.sum(), x64.sum(axis=(1, 2)), x.sum(),
        (x64 * w[None, :, None]).sum(axis=(1, 2)), x.min(), x.max(), da.argmax(x))
    L["reduce_s"] = round(time.time() - t0, 1)
    L["dask_tasks_per_input"] = x.npartitions
    check("%s: exact (day, row) integer sums == reference, all 10.8M" % name, np.array_equal(rows, R))
    check("%s: min/max bit-exact" % name, float(mn) == amin and float(mx) == amax)
    if amax_idx is not None:
        check("%s: argmax of the unique maximum == numpy" % name, int(am) == amax_idx, int(am))
    else:
        L["argmax"] = int(am)
    be = float(abs(fractions.Fraction(float(tot64)) - truth) / truth)
    bound = 2 * math.log2(N) * EPS
    check("%s: float64 total within 2*log2(n)*eps of exact" % name, be <= bound,
          "%.2e <= %.2e, %d ulp from correctly rounded" % (be, bound,
                                                          round(abs(float(tot64) - truth_f) / math.ulp(truth_f))))
    be32 = float(abs(fractions.Fraction(float(tot32)) - truth) / truth)
    check("%s: control -- float32 accumulation misses that bound" % name, be32 > bound,
          "%.2e, global mean off by %.2e K" % (be32, be32 * truth_f / N))
    dref = D.astype(np.float64) / SCALE           # exact
    dbe = np.abs(day64 - dref) / dref
    if EXACT_DAYS:
        # every value >= 128 K is a multiple of 2**-16, and no partial sum of one day reaches
        # 2**37, so every partial sum is exactly representable in float64: ANY order is exact
        check("%s: every daily float64 sum EXACT (no rounding is possible)" % name,
              np.array_equal(day64, dref), "max rel err %.1e" % float(dbe.max()))
    else:
        dbound = 2 * math.log2(51840) * EPS
        check("%s: every daily float64 sum within 2*log2(51840)*eps" % name, float(dbe.max()) <= dbound,
              "max %.2e <= %.2e" % (float(dbe.max()), dbound))
    absW = np.array([math.fsum(row) for row in np.abs(P)])
    wbe = np.abs(wsum - W_ref) / absW
    wbound = (2 * math.log2(51840) + 2) * EPS
    check("%s: every daily area-weighted sum within (2*log2(51840)+2)*eps" % name, float(wbe.max()) <= wbound,
          "max %.2e <= %.2e" % (float(wbe.max()), wbound))
    vals[name] = {"tot64": float(tot64).hex(), "tot32": float(tot32).hex(),
                  "day64": hashlib.sha256(day64.tobytes()).hexdigest()[:16],
                  "wsum": hashlib.sha256(np.ascontiguousarray(wsum).tobytes()).hexdigest()[:16]}
    L.update(vals[name])
    L["tot64_be"] = "%.3e" % be
    L["tot32_be"] = "%.3e" % be32
    L["day64_max_be"] = "%.3e" % float(dbe.max())
    L["rss_gib"] = rss_gib()
    RESULT["layouts"][name] = L
    print("     %s  %s" % (name, json.dumps(L)), flush=True)
    if name != "v2-native":            # v3-native compares against it; drop it after
        shutil.rmtree(path, ignore_errors=True)
    if name == "v3-native":
        shutil.rmtree(os.path.join(WORK, "v2-native.zarr"), ignore_errors=True)

# which float results moved with the layout (a path, reported -- not asserted)
for k in ("tot64", "tot32", "day64", "wsum"):
    obs("distinct_%s_across_7_layouts" % k, len({v[k] for v in vals.values()}))
# determinism: the same reduction twice on the published store
s1 = da.from_zarr(z).astype(np.float64).sum(axis=(1, 2)).compute()
s2 = da.from_zarr(z).astype(np.float64).sum(axis=(1, 2)).compute()
check("determinism: the same dask reduction twice is bit-identical", np.array_equal(s1, s2))

RESULT["wall_s"] = round(time.time() - T_ALL, 1)
RESULT["ru_maxrss_gib"] = rss_gib()
RESULT["ok"] = OK
json.dump(RESULT, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
print("wall %.0f s, peak RSS %.2f GiB" % (RESULT["wall_s"], RESULT["ru_maxrss_gib"]), flush=True)
if not OK:
    print("ZARR-DASK-REAL FAILED")
    sys.exit(1)
print("ZARR-DASK-REAL OK")
