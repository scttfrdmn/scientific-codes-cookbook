"""earth-observation-real: a whole year of one Sentinel-2 tile through rasterio, with the identities.

    python3 -u real.py <data dir> <workers> <out dir> [stride]

<data dir> holds pins.sha256, scenes.txt, sizes.tsv and items/<id>.json. The pixels are read in
place from the RODA `sentinel-cogs` bucket over HTTPS: each object is pulled once into memory,
hashed and held to its pin before rasterio sees it. `stride` > 1 runs every stride-th scene of the
size-ordered list (the sizing probe); the measurement runs all 153.

Per scene: grids equal the STAC item's; rasterio == tifffile/imagecodecs on every pixel of B04,
B08 and SCL; block-windowed reads conserve the full read; ESA's published percentages are
reproduced from the pixels; NDVI from scaled reflectance stays in [-1, 1]; the classifier-vs-index
ordering. Over the year: a greenest-pixel (max NDVI) composite of clear-sky observations at 10 m,
whose observation count conserves the per-scene clear counts, written as a COG by rasterio and read
back exactly by rasterio and by tifffile. Writes score.tsv, scenes.tsv, real.log lines, and the two
COGs to <out dir>. Prints REAL OK only if every check passed.
"""
import hashlib
import io
import json
import multiprocessing as mp
import os
import resource
import sys
import time
import urllib.request
import warnings

import numpy as np

# rasterio 1.5.0 sets ndarray.shape, which numpy 2.5 deprecates; one line per read, not a result
warnings.filterwarnings("ignore", message="Setting the shape on a NumPy array")

BASE = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
N10, N20 = 10980, 5490
# SCL codes, L2A product definition, and the STAC property each one's percentage is published as
SCL = {1: "saturated_defective_pixel", 2: "dark_features", 3: "cloud_shadow", 4: "vegetation",
       5: "not_vegetated", 6: "water", 7: "unclassified", 8: "medium_proba_clouds",
       9: "high_proba_clouds", 10: "thin_cirrus", 11: "snow_ice"}
CLEAR = (4, 5, 6)   # vegetation, bare, water: the composite's clear-sky observations

DATA, W, OUT = sys.argv[1], int(sys.argv[2]), sys.argv[3]
STRIDE = int(sys.argv[4]) if len(sys.argv) > 4 else 1
os.makedirs(OUT, exist_ok=True)


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


def fetch(path):
    err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(BASE + path, timeout=180) as r:
                want = int(r.headers["Content-Length"])
                buf = r.read()
            if len(buf) != want:
                raise IOError("short read %d of %d" % (len(buf), want))
            return buf
        except Exception as e:  # transient network error: retry; a third failure fails the scene
            err = e
            time.sleep(2)
    raise IOError("%s: %s" % (path, err))


def blocksum(a):
    """2x2 block sum by four elementwise adds: the same IEEE operations on every chip."""
    return a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]


def scene_work(scene, pins, comp, cnt):
    import rasterio
    import tifffile
    from rasterio.io import MemoryFile
    sid = scene.rsplit("/", 1)[1]
    r = {"scene": sid}
    bad = []

    def chk(name, ok, detail=""):
        if not bool(ok):
            bad.append("%s %s" % (name, detail))

    t0 = time.time()
    raw = open(os.path.join(DATA, "items", sid + ".json"), "rb").read()
    chk("pin_item", hashlib.sha256(raw).hexdigest() == pins[sid + "/item.json"])
    item = json.loads(raw)
    props = item["properties"]
    blobs = {}
    for f in ("B04.tif", "B08.tif", "SCL.tif"):
        b = fetch(scene + "/" + f)
        chk("pin_" + f, hashlib.sha256(b).hexdigest() == pins[sid + "/" + f])
        blobs[f] = b
    r["bytes"] = sum(len(b) for b in blobs.values())
    r["t_fetch"] = time.time() - t0

    t1 = time.time()
    arr, meta = {}, {}
    for f, key in (("B04.tif", "red"), ("B08.tif", "nir"), ("SCL.tif", "scl")):
        a = item["assets"][key]
        with MemoryFile(blobs[f]) as mf, mf.open() as s:
            x = s.read(1)
            meta[f] = (s.shape, s.crs, s.transform, s.dtypes[0])
            epsg = a.get("proj:epsg", props.get("proj:epsg"))
            if epsg is None:
                epsg = int(str(a.get("proj:code", props.get("proj:code"))).split(":")[1])
            tr = a["proj:transform"]
            chk("grid_" + f, list(s.shape) == a["proj:shape"] and s.crs.to_epsg() == epsg
                and all(abs(u - v) == 0 for u, v in zip(tuple(s.transform)[:6], tr[:6])),
                "%s %s %s vs %s %s %s" % (s.shape, s.crs.to_epsg(), tuple(s.transform)[:6],
                                         a["proj:shape"], epsg, tr[:6]))
            # windowed reads over the COG's own internal blocks must conserve the full read
            if f == "SCL.tif":
                bc = np.zeros(256, np.int64)
                for _, win in s.block_windows(1):
                    bc += np.bincount(s.read(1, window=win).ravel(), minlength=256)
                full = np.bincount(x.ravel(), minlength=256)
                chk("windows_SCL", np.array_equal(bc, full))
            elif f == "B04.tif":
                tot = 0
                for _, win in s.block_windows(1):
                    tot += int(s.read(1, window=win).sum(dtype=np.uint64))
                r["b04_sum"] = int(x.sum(dtype=np.uint64))
                chk("windows_B04", tot == r["b04_sum"], "%d vs %d" % (tot, r["b04_sum"]))
        # an independent TIFF parser and codec on the same bytes: every pixel equal
        y = tifffile.imread(io.BytesIO(blobs[f]), key=0, maxworkers=1)
        chk("tifffile_" + f, y.dtype == x.dtype and y.shape == x.shape and np.array_equal(x, y))
        del y
        arr[f] = x
    chk("grids_nest", meta["B04.tif"][0] == meta["B08.tif"][0] == (N10, N10)
        and meta["SCL.tif"][0] == (N20, N20) and meta["B04.tif"][2] == meta["B08.tif"][2]
        and meta["B04.tif"][2].c == meta["SCL.tif"][2].c and meta["B04.tif"][2].f == meta["SCL.tif"][2].f)
    del blobs
    r["crs"], r["transform"] = meta["B04.tif"][1].to_wkt(), tuple(meta["B04.tif"][2])[:6]
    r["t_decode"] = time.time() - t1

    # ---- ESA's published percentages, reproduced from the pixels -------------------------
    t2 = time.time()
    red, nir, scl = arr["B04.tif"], arr["B08.tif"], arr["SCL.tif"]
    counts = np.bincount(scl.ravel(), minlength=256)
    chk("scl_codes", counts[12:].sum() == 0, "codes > 11 present")
    valid20 = int(N20 * N20 - counts[0])
    r["scl_valid"] = valid20
    r["scl_counts"] = ",".join(str(int(c)) for c in counts[:12])
    # ESA's percentages carry float32 precision, then a 6-decimal print. The bound on each is half
    # the print ulp plus every rounding a float32 evaluation of 100 * c / n can make -- converting
    # c and n, the divide, the scale: four half-ulps, relative 4 x 2^-24. `ratio` is the worst
    # |computed - published| / bound, so <= 1 passes; `px` is the same residual in pixels; `exact`
    # counts values whose float64 percentage rounds to the published six decimals exactly.
    worst, wname, wpx, npub, nexact, n1 = 0.0, "", 0.0, 0, 0, 0

    def pct_check(got, pub, denom, name):
        nonlocal worst, wname, wpx, npub, nexact, n1
        npub += 1
        nexact += round(got, 6) == pub
        d = abs(got - pub)
        n1 += d > 5e-7 + abs(pub) * 2.0 ** -24          # more than ONE float32 rounding
        ratio = d / (5e-7 + abs(pub) * 4 * 2.0 ** -24)
        if ratio > worst:
            worst, wname, wpx = ratio, name, d * denom / 100.0

    for code, name in SCL.items():
        pub = props.get("s2:%s_percentage" % name)
        if pub is None:
            continue
        pct_check(100.0 * counts[code] / valid20 if valid20 else 0.0, pub, valid20, name)
    # nodata: SCL == 0 over the whole 5490^2 grid
    pct_check(100.0 * int(counts[0]) / (N20 * N20), props["s2:nodata_pixel_percentage"], N20 * N20, "nodata_pixel")
    r["pct_published"] = npub
    r["pct_exact"] = nexact
    r["pct_over_one_rounding"] = n1
    r["pct_ratio"] = worst
    r["pct_worst"] = wname
    r["pct_worst_px"] = wpx

    z_r, z_n = red == 0, nir == 0
    valid = ~(z_r | z_n)
    del z_r, z_n

    # ---- the recipe's own method, unchanged: NDVI on raw DN, 2x2 mean onto the SCL grid -------
    rf = red.astype(np.float32)
    nf = nir.astype(np.float32)
    den = nf + rf
    raw = nf - rf            # zero wherever den is: both DNs are non-negative
    np.divide(raw, den, out=raw, where=den > 0)
    del den
    agg = blocksum(raw) * np.float32(0.25)
    del raw
    for code, nm in ((4, "veg"), (5, "bare"), (6, "water")):
        m = scl == code
        n = int(m.sum())
        if n:
            v = agg[m]
            r["raw_mean_" + nm] = float(v.mean(dtype=np.float64))
            r["raw_p10_" + nm] = float(np.percentile(v, 10))
            r["raw_p90_" + nm] = float(np.percentile(v, 90))
    del agg
    # NIR below red over water does not depend on the offset: it is the sign of NDVI either way
    wm10 = np.broadcast_to((scl == 6)[:, None, :, None], (N20, 2, N20, 2)).reshape(N10, N10) & valid
    nw = int(wm10.sum())
    r["water_px10"] = nw
    if nw:
        r["water_nir_lt_red"] = int((nir[wm10] < red[wm10]).sum())
    del wm10, red, nir
    arr.clear()

    # ---- NDVI from reflectance (DN * scale + offset, from the item's raster:bands) ----------
    rb = item["assets"]["red"]["raster:bands"][0]
    nb = item["assets"]["nir"]["raster:bands"][0]
    r["scale_offset"] = "%s/%s" % (rb.get("scale"), rb.get("offset"))
    rf *= np.float32(rb.get("scale", 1.0))
    rf += np.float32(rb.get("offset", 0.0))
    nf *= np.float32(nb.get("scale", 1.0))
    nf += np.float32(nb.get("offset", 0.0))
    valid &= rf > 0
    valid &= nf > 0          # NDVI is in [-1, 1] by algebra when both reflectances are positive
    ndvi = nf - rf
    nf += rf                 # nf is now the denominator
    del rf
    np.divide(ndvi, nf, out=ndvi, where=valid)
    ndvi[~valid] = np.nan
    del nf
    nv = int(valid.sum())
    r["ndvi_valid"] = nv
    if nv:
        vmin, vmax = float(np.nanmin(ndvi)), float(np.nanmax(ndvi))
        chk("ndvi_in_range", -1.0 <= vmin and vmax <= 1.0, "[%r, %r]" % (vmin, vmax))
        r["ndvi_min"], r["ndvi_max"] = vmin, vmax
        r["ndvi_sum"] = float(np.nansum(ndvi, dtype=np.float64))
    # aggregate 2x2 onto the classification grid, only where all four 10 m pixels are valid
    four = blocksum(valid.astype(np.uint8)) == 4
    agg = blocksum(np.where(valid, ndvi, np.float32(0))) * np.float32(0.25)
    for code, nm in ((4, "veg"), (5, "bare"), (6, "water")):
        m = (scl == code) & four
        n = int(m.sum())
        r["n_" + nm] = n
        if n:
            v = agg[m]
            r["mean_" + nm] = float(v.mean(dtype=np.float64))
            r["p10_" + nm] = float(np.percentile(v, 10))
            r["p90_" + nm] = float(np.percentile(v, 90))
    del agg, four

    # ---- the year's greenest-pixel composite: this worker's share ---------------------------
    clear20 = np.isin(scl, CLEAR)
    use = np.broadcast_to(clear20[:, None, :, None], (N20, 2, N20, 2)).reshape(N10, N10) & valid
    del clear20, valid
    r["clear_obs"] = int(use.sum())
    np.maximum(comp, ndvi, out=comp, where=use)
    cnt += use
    r["t_science"] = time.time() - t2
    r["bad"] = bad
    return r


def worker(i, q, res, pins):
    os.environ["GDAL_NUM_THREADS"] = "1"
    comp = np.full((N10, N10), -np.inf, np.float32)
    cnt = np.zeros((N10, N10), np.uint8)
    n = 0
    while True:
        scene = q.get()
        if scene is None:
            break
        try:
            res.put(scene_work(scene, pins, comp, cnt))
        except Exception as e:
            res.put({"scene": scene.rsplit("/", 1)[1], "bad": ["exception %r" % (e,)]})
        n += 1
    np.save("/var/tmp/eo-comp-%d.npy" % i, comp)
    np.save("/var/tmp/eo-cnt-%d.npy" % i, cnt)
    res.put({"worker_done": i, "scenes": n,
             "maxrss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0})


def main():
    import imagecodecs
    import rasterio
    import tifffile
    T0 = time.time()
    fail = []
    score = []

    def check(label, ok, detail):
        print("%-34s %s%s" % (label, detail, "" if bool(ok) else "   <-- FAIL"))
        if not bool(ok):
            fail.append(label)

    def put(k, v):
        score.append((k, str(v)))

    print("rasterio %s  GDAL %s  tifffile %s  imagecodecs %s  numpy %s  python %s" % (
        ver(rasterio), str(getattr(rasterio, "__gdal_version__", "unknown")), ver(tifffile),
        ver(imagecodecs), ver(np), sys.version.split()[0]))
    pins = {}
    for line in open(os.path.join(DATA, "pins.sha256")):
        h, name = line.split()
        pins[name] = h
    scenes = [l.strip() for l in open(os.path.join(DATA, "scenes.txt")) if l.strip()]
    check("scenes", len(scenes) == len(set(scenes)) == 153 and
          all(s.rsplit("/", 1)[1] + "/" + f in pins for s in scenes
              for f in ("item.json", "B04.tif", "B08.tif", "SCL.tif")),
          "%d distinct scene ids, each with 4 pins  want 153" % len(set(scenes)))
    size = {}
    for line in open(os.path.join(DATA, "sizes.tsv")):
        name, n = line.split("\t")
        sid = name.split("/")[0]
        size[sid] = size.get(sid, 0) + int(n)
    order = sorted(scenes, key=lambda s: -size[s.rsplit("/", 1)[1]])
    run = order[::STRIDE]
    print("running %d of %d scenes, %.2f GB, on %d workers" % (
        len(run), len(scenes), sum(size[s.rsplit("/", 1)[1]] for s in run) / 1e9, W))

    ctx = mp.get_context("fork")
    q, res = ctx.Queue(), ctx.Queue()
    for s in run:
        q.put(s)
    for _ in range(W):
        q.put(None)
    procs = [ctx.Process(target=worker, args=(i, q, res, pins)) for i in range(W)]
    for p in procs:
        p.start()
    rows, done = [], []
    while len(done) < W:
        m = res.get()
        if "worker_done" in m:
            done.append(m)
        else:
            rows.append(m)
            print("  %-26s %s" % (m["scene"], "ok" if not m["bad"] else "BAD: " + "; ".join(m["bad"])),
                  flush=True)
    for p in procs:
        p.join()
    t_scenes = time.time() - T0
    rows.sort(key=lambda m: m["scene"])
    nbad = [m for m in rows if m["bad"]]
    check("every_scene_clean", len(rows) == len(run) and not nbad,
          "%d of %d scenes passed pins, grids, tifffile, windows, scl codes, ndvi range%s" % (
              len(rows) - len(nbad), len(run), "" if not nbad else "  bad: " +
              ", ".join(m["scene"] for m in nbad[:10])))

    # ---- ESA's published percentages ---------------------------------------------------------
    ok = [m for m in rows if "pct_ratio" in m]
    wpct = max(ok, key=lambda m: m["pct_ratio"])
    check("pct_reproduce_published", len(ok) == len(run) and all(m["pct_ratio"] <= 1.0 for m in ok),
          "%d published percentages over %d scenes; worst |computed - published| = %.2f x the bound "
          "(%s, %s, %.2f px)  want <= 1, bound = 5e-7 + 4 x 2^-24 x value (a float32 100*c/n, printed to 6 dp)" % (
              sum(m["pct_published"] for m in ok), len(ok), wpct["pct_ratio"], wpct["scene"],
              wpct["pct_worst"], wpct["pct_worst_px"]))
    put("pct_published", sum(m["pct_published"] for m in ok))
    put("pct_worst_ratio", "%.4f" % wpct["pct_ratio"])
    put("pct_exact", sum(m["pct_exact"] for m in ok))
    put("pct_over_one_rounding", sum(m["pct_over_one_rounding"] for m in ok))
    print("%-34s %d of %d round to the published 6 decimals exactly; %d exceed one float32 rounding   [observation]" % (
        "pct_exact", sum(m["pct_exact"] for m in ok), sum(m["pct_published"] for m in ok),
        sum(m["pct_over_one_rounding"] for m in ok)))

    # ---- classifier vs index ordering, and water: reported, under both NDVI methods -----------
    for tag, pre in (("reflectance", ""), ("recipe_raw_dn", "raw_")):
        both = [m for m in ok if (pre + "p10_veg") in m and (pre + "p90_bare") in m]
        held = [m for m in both if m[pre + "p10_veg"] > m[pre + "p90_bare"]]
        print("%-34s p10(veg) > p90(bare) on %d of %d scenes with both classes   [observation]" % (
            "order_" + tag, len(held), len(both)))
        put("order_held_" + tag, "%d/%d" % (len(held), len(both)))
    wat = [m for m in ok if "raw_mean_water" in m]
    wneg = [m for m in wat if m["raw_mean_water"] < 0]
    print("%-34s mean raw-DN NDVI(water) < 0 on %d of %d scenes with water   [observation]" % (
        "water_raw_negative", len(wneg), len(wat)))
    put("water_raw_negative", "%d/%d" % (len(wneg), len(wat)))
    wpx = sum(m.get("water_px10", 0) for m in ok)
    wlt = sum(m.get("water_nir_lt_red", 0) for m in ok)
    print("%-34s NIR < red on %d of %d ESA-water 10 m px (%.2f%%), offset-independent   [observation]" % (
        "water_nir_below_red", wlt, wpx, 100.0 * wlt / max(wpx, 1)))
    put("water_nir_lt_red", "%d/%d" % (wlt, wpx))

    # ---- the composite: reduce, conserve, write, read back ----------------------------------
    t3 = time.time()
    comp = np.full((N10, N10), -np.inf, np.float32)
    cnt = np.zeros((N10, N10), np.uint16)
    for i in range(W):
        np.maximum(comp, np.load("/var/tmp/eo-comp-%d.npy" % i), out=comp)
        cnt += np.load("/var/tmp/eo-cnt-%d.npy" % i)
        os.remove("/var/tmp/eo-comp-%d.npy" % i)
        os.remove("/var/tmp/eo-cnt-%d.npy" % i)
    obs = sum(m.get("clear_obs", 0) for m in rows)
    check("composite_count_conserves", int(cnt.sum(dtype=np.uint64)) == obs,
          "sum(count) = %d == sum over scenes of clear observations %d" % (int(cnt.sum(dtype=np.uint64)), obs))
    check("composite_where_observed", bool(np.all(np.isfinite(comp) == (cnt > 0))),
          "the composite is finite exactly where the count is > 0")
    comp[cnt == 0] = np.nan
    cmin, cmax = float(np.nanmin(comp)), float(np.nanmax(comp))
    check("composite_in_range", -1.0 <= cmin and cmax <= 1.0, "[%.4f, %.4f]" % (cmin, cmax))
    hc = hashlib.sha256(comp.tobytes()).hexdigest()
    hn = hashlib.sha256(cnt.astype(np.uint8).tobytes()).hexdigest()
    put("composite_array_sha256", hc)
    put("count_array_sha256", hn)
    put("composite_px_observed", int((cnt > 0).sum()))
    put("count_max", int(cnt.max()))
    put("composite_mean", "%.9f" % float(np.nanmean(comp, dtype=np.float64)))
    print("%-34s %d px observed, max %d obs/px, mean %.6f   [observation]" % (
        "composite", int((cnt > 0).sum()), int(cnt.max()), float(np.nanmean(comp, dtype=np.float64))))

    from affine import Affine
    from rasterio.crs import CRS
    grids = {(m["crs"], m["transform"]) for m in rows if "crs" in m}
    check("one_grid_all_year", len(grids) == 1, "%d distinct (CRS, transform) over the year  want 1" % len(grids))
    g = sorted(grids)[0]
    crs, tr = CRS.from_wkt(g[0]), Affine(*g[1])
    prof = dict(driver="COG", width=N10, height=N10, count=1, crs=crs, transform=tr,
                compress="DEFLATE", blocksize=512, num_threads=str(W))
    cpath, npath = os.path.join(OUT, "ndvi-max-2024.tif"), os.path.join(OUT, "ndvi-count-2024.tif")
    with rasterio.open(cpath, "w", dtype="float32", nodata=float("nan"), predictor=3, **prof) as d:
        d.write(comp, 1)
    with rasterio.open(npath, "w", dtype="uint8", nodata=None, predictor=2, **prof) as d:
        d.write(cnt.astype(np.uint8), 1)
    t_write = time.time() - t3
    with rasterio.open(cpath) as s:
        back = s.read(1)
        nov = len(s.overviews(1))
        cog_ok = s.profile.get("tiled", False) and s.crs == crs and s.transform == tr
    import tifffile as tf
    back2 = tf.imread(cpath, key=0, maxworkers=1)
    with rasterio.open(npath) as s:
        nback = s.read(1)
    check("cog_roundtrip_rasterio", np.array_equal(back, comp, equal_nan=True)
          and np.array_equal(nback, cnt.astype(np.uint8)),
          "both COGs read back equal to the in-memory arrays, every pixel")
    check("cog_roundtrip_tifffile", np.array_equal(back2, comp, equal_nan=True),
          "tifffile reads rasterio's float32 COG equal, every pixel")
    check("cog_structure", cog_ok and nov >= 4, "tiled, same CRS and transform, %d overviews" % nov)
    put("cog_ndvi_bytes", os.path.getsize(cpath))
    put("cog_ndvi_sha256", hashlib.sha256(open(cpath, "rb").read()).hexdigest())

    # ---- per-scene table (fixed values only) and score ---------------------------------------
    cols = ["scene", "bytes", "scl_valid", "scl_counts", "pct_published", "pct_exact", "pct_ratio", "pct_worst",
            "scale_offset", "b04_sum", "ndvi_valid", "ndvi_min", "ndvi_max",
            "ndvi_sum", "n_veg", "mean_veg", "p10_veg", "p90_veg", "n_bare", "mean_bare", "p10_bare",
            "p90_bare", "n_water", "mean_water", "raw_mean_veg", "raw_p10_veg", "raw_p90_veg",
            "raw_mean_bare", "raw_p10_bare", "raw_p90_bare", "raw_mean_water", "water_px10",
            "water_nir_lt_red", "clear_obs"]
    with open(os.path.join(OUT, "scenes.tsv"), "w") as fh:
        fh.write("\t".join(cols) + "\n")
        for m in rows:
            fh.write("\t".join(repr(float(m[c])) if isinstance(m.get(c), float) else str(m.get(c, ""))
                               for c in cols) + "\n")
    put("scenes_run", len(rows))
    put("bytes_read", sum(m.get("bytes", 0) for m in rows))
    put("scenes_tsv_sha256", hashlib.sha256(open(os.path.join(OUT, "scenes.tsv"), "rb").read()).hexdigest())
    put("clear_obs_total", obs)
    wall = time.time() - T0
    put("wall_s_scenes", "%.1f" % t_scenes)
    put("wall_s_composite", "%.1f" % t_write)
    put("wall_s_total", "%.1f" % wall)
    put("sum_t_fetch", "%.1f" % sum(m.get("t_fetch", 0) for m in rows))
    put("sum_t_decode", "%.1f" % sum(m.get("t_decode", 0) for m in rows))
    put("sum_t_science", "%.1f" % sum(m.get("t_science", 0) for m in rows))
    put("peak_rss_gib_worker", "%.2f" % max(d["maxrss_gib"] for d in done))
    put("peak_rss_gib_main", "%.2f" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0))
    with open(os.path.join(OUT, "score.tsv"), "w") as fh:
        fh.write("key\tvalue\n")
        for k, v in score:
            fh.write("%s\t%s\n" % (k, v))
    for k, v in score:
        print("  %-28s %s" % (k, v))
    if fail:
        print("FAILED: " + ", ".join(fail))
        sys.exit(1)
    print("REAL OK")


if __name__ == "__main__":
    main()
