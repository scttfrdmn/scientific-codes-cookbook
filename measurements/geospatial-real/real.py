#!/usr/bin/env python3
"""geospatial-real: the whole state of Colorado (Copernicus GLO-30, 28 tiles) through GDAL.

The workload is what a terrain study does first: mosaic the tiles, reproject to UTM 13N at
30 m, then slope and hillshade. The checks carry the recipe's identities to that scale:
PROJ's own 6,191 reference vectors, GDAL's `-r sum` against a block reduction (exact grid),
`-r near` against an independent pyproj inverse of every output pixel, a projection
round-trip, and `gdaldem slope` against Horn's formula in numpy. Writes /tmp/out/results.json,
/tmp/out/smoke-check.txt and /tmp/out/samples.npz (cross-chip comparison), and prints
GEOSPATIAL REAL OK only if every check passed.
"""
import ctypes
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time

import numpy as np

NT = 16                       # threads for every multi-threaded step, the same on every chip
WORK, OUT = "/var/tmp/work", "/tmp/out"
os.makedirs(WORK, exist_ok=True)
os.makedirs(OUT, exist_ok=True)
R, FAIL, LINES = {}, [], []
T_START = time.time()


def rec(k, v):
    # every non-numeric value through str(): a line that only describes the run must not be
    # able to fail it
    if isinstance(v, (bool, np.bool_)):
        v = bool(v)
    elif isinstance(v, (int, np.integer)):
        v = int(v)
    elif isinstance(v, (float, np.floating)):
        v = float(v)
    elif not isinstance(v, (list, dict)) and v is not None:
        v = str(v)
    R[k] = v


def check(label, ok, detail):
    line = "%-34s %s" % (label, detail)
    print(line, flush=True)
    LINES.append(line)
    if not bool(ok):
        FAIL.append(label)


def obs(label, detail):
    line = "%-34s %s   [observation]" % (label, detail)
    print(line, flush=True)
    LINES.append(line)


def run(label, argv, cwd=None):
    t = time.time()
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    el = time.time() - t
    with open(os.path.join(OUT, "commands.log"), "a") as f:
        f.write("== %s (%.1fs, rc %d)\n%s\n%s%s\n" % (label, el, p.returncode, " ".join(argv),
                                                     p.stdout[-4000:], p.stderr[-4000:]))
    rec("t_" + label, round(el, 2))
    print("[%s] %.1fs rc=%d" % (label, el, p.returncode), flush=True)
    return p


def describe():
    def safe(f):
        try:
            return f()
        except Exception as e:  # noqa: BLE001 -- describing the run must never fail it
            return "unknown (%s)" % type(e).__name__
    txt = safe(lambda: open("/proc/cpuinfo").read())
    m = re.search(r"model name\s*:\s*(.+)", txt) if isinstance(txt, str) else None
    part = re.search(r"CPU part\s*:\s*(\S+)", txt) if isinstance(txt, str) else None
    rec("cpu_model", m.group(1).strip() if m else ("arm CPU part " + part.group(1) if part else "unknown"))

    def tpc():
        s = open("/sys/devices/system/cpu/cpu0/topology/thread_siblings_list").read().strip()
        n = 0
        for tok in s.split(","):
            a, _, b = tok.partition("-")
            n += (int(b) - int(a) + 1) if b else 1
        return n
    rec("threads_per_core", safe(tpc))
    rec("nproc", os.cpu_count())
    rec("mem_total_gib", safe(lambda: round(int(re.search(r"MemTotal:\s+(\d+)", open("/proc/meminfo").read()).group(1)) / 1048576, 1)))

    def core():
        lib = ctypes.CDLL(sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))[0])
        lib.openblas_get_corename.restype = ctypes.c_char_p
        return lib.openblas_get_corename().decode()
    rec("openblas_corename", safe(core))
    import pyproj
    import rasterio
    from osgeo import gdal
    rec("gdal", getattr(gdal, "__version__", "unknown"))
    rec("rasterio", getattr(rasterio, "__version__", "unknown"))
    rec("pyproj", getattr(pyproj, "__version__", "unknown"))
    rec("proj", getattr(pyproj, "proj_version_str", "unknown"))
    rec("numpy", getattr(np, "__version__", "unknown"))


def digest(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def main():
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import Window

    describe()
    obs("versions", "GDAL %s / rasterio %s / pyproj %s on PROJ %s / numpy %s" %
        (R["gdal"], R["rasterio"], R["pyproj"], R["proj"], R["numpy"]))
    obs("cpu", "%s, %s vCPU, %s thread(s)/core, %s GiB, OpenBLAS %s" %
        (R["cpu_model"], R["nproc"], R["threads_per_core"], R["mem_total_gib"], R["openblas_corename"]))

    # ---- PROJ's own committed reference vectors (the recipe's check, unchanged) ---------
    gdir = os.path.join(WORK, "gie")
    os.makedirs(gdir, exist_ok=True)
    subprocess.run(["tar", "-xf", "/tmp/gie.tar", "-C", gdir], check=True)
    p = run("gie", ["sh", "-c", "gie *.gie"], cwd=gdir)
    open(os.path.join(OUT, "gie.out"), "w").write(p.stdout + p.stderr)
    m = re.search(r"Grand total:\s*(\d+)\.\s*Success:\s*(\d+),\s*Skipped:\s*(\d+),\s*Failure:\s*(\d+)",
                  p.stdout + p.stderr)
    tot, ok, skip, bad = (int(g) for g in m.groups()) if m else (0, 0, 0, -1)
    check("gie_reference_vectors", m is not None and tot == 6191 and bad == 0 and skip == 0,
          "%d total / %d ok / %d skipped / %d failed  want 6191 / 0 / 0" % (tot, ok, skip, bad))

    # ---- the mosaic ----------------------------------------------------------------------
    tiles = sorted(glob.glob("/tmp/Copernicus_DSM_COG_10_*_DEM.tif"))
    vrt = os.path.join(WORK, "colorado.vrt")
    p = run("buildvrt", ["gdalbuildvrt", "-overwrite", vrt] + tiles)
    if p.returncode:
        FAIL.append("buildvrt")
        return
    with rasterio.open(vrt) as ds:
        vt, vshape, vnod, vcrs = ds.transform, ds.shape, ds.nodata, ds.crs
        t = time.time()
        M = ds.read(1)
        rec("t_read_mosaic", round(time.time() - t, 2))
    res = vt.a
    # every tile must sit an exact whole number of pixels from the mosaic origin, or the
    # mosaic is a resample, not a concatenation
    offs = []
    for f in tiles:
        with rasterio.open(f) as d:
            tt = d.transform
            offs.append(((tt.c - vt.c) / res, (vt.f - tt.f) / res, d.shape, d.dtypes[0], d.nodata))
    aligned = all(abs(c - round(c)) < 1e-6 and abs(r - round(r)) < 1e-6 and s == (3600, 3600)
                  for c, r, s, _, _ in offs)
    check("mosaic_shape", len(tiles) == 28 and vshape == (14400, 25200) and aligned,
          "%d tiles -> %s px, every tile on a whole-pixel offset: %s  want 28 -> (14400, 25200)" %
          (len(tiles), vshape, aligned))
    rec("mosaic_crs", vcrs.to_string() if vcrs else None)
    rec("tile_dtype_nodata", "%s / %s" % (offs[0][3], offs[0][4]))
    valid = np.isfinite(M) if vnod is None else (M != vnod) & np.isfinite(M)
    rec("mosaic_pixels", M.size)
    rec("mosaic_invalid", int(M.size - valid.sum()))
    rec("mosaic_min", float(M[valid].min()))
    rec("mosaic_max", float(M[valid].max()))
    rec("sha_mosaic", digest(M))
    obs("mosaic", "%s, %s, nodata %s; %d invalid px of %d; elevation %.2f .. %.2f m" %
        (R["mosaic_crs"], R["tile_dtype_nodata"], vnod, R["mosaic_invalid"], M.size,
         R["mosaic_min"], R["mosaic_max"]))
    co = ["-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3", "-co", "BIGTIFF=YES",
          "-co", "NUM_THREADS=%d" % NT]

    # ---- the workload: reproject the state to UTM 13N at 30 m, then terrain ----------------
    dem = os.path.join(WORK, "dem_utm13_30m.tif")
    p = run("warp_bilinear", ["gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:32613", "-tr", "30", "30",
                              "-tap", "-r", "bilinear", "-multi", "-wo", "NUM_THREADS=%d" % NT,
                              "-wm", "1024", "-dstnodata", "-32768", "-ot", "Float32"] + co + [vrt, dem])
    if p.returncode:
        FAIL.append("warp_bilinear")
        return
    with rasterio.open(dem) as d:
        ut, (H, W) = d.transform, d.shape
    rec("utm_shape", [H, W])
    slope = os.path.join(WORK, "slope.tif")
    hs = os.path.join(WORK, "hillshade.tif")
    p1 = run("slope", ["gdaldem", "slope", "-q", dem, slope] + co)
    p2 = run("hillshade", ["gdaldem", "hillshade", "-q", dem, hs, "-co", "TILED=YES",
                           "-co", "COMPRESS=DEFLATE", "-co", "BIGTIFF=YES"])
    if p1.returncode or p2.returncode:
        FAIL.append("gdaldem")
        return
    rec("t_workload", round(R["t_warp_bilinear"] + R["t_slope"] + R["t_hillshade"], 2))
    obs("workload", "UTM 13N 30 m: %d x %d px; warp %.1fs, slope %.1fs, hillshade %.1fs" %
        (W, H, R["t_warp_bilinear"], R["t_slope"], R["t_hillshade"]))

    # ---- identity 1: -r sum on an exact 10x grid == block sums ---------------------------
    s10 = os.path.join(WORK, "sum10.tif")
    l, b, r_, t_ = vt.c, vt.f + vt.e * vshape[0], vt.c + vt.a * vshape[1], vt.f
    p = run("warp_sum", ["gdalwarp", "-q", "-overwrite", "-r", "sum", "-ovr", "NONE",
                         "-te", repr(l), repr(b), repr(r_), repr(t_), "-ts", "2520", "1440",
                         "-multi", "-wo", "NUM_THREADS=%d" % NT, "-wm", "1024", "-ot", "Float64", vrt, s10])
    if p.returncode:
        FAIL.append("warp_sum")
        return
    with rasterio.open(s10) as d:
        G = d.read(1)
    t = time.time()
    Mv = np.where(valid, M, 0).astype(np.float64)
    ref = Mv.reshape(1440, 10, 2520, 10).sum(axis=(1, 3))
    absblk = np.abs(Mv).reshape(1440, 10, 2520, 10).sum(axis=(1, 3))
    del Mv
    rec("t_numpy_blocksum", round(time.time() - t, 2))
    rel = float((np.abs(G - ref) / np.maximum(absblk, 1.0)).max())
    rec("sum_max_abs_diff", float(np.abs(G - ref).max()))
    rec("sum_max_rel_diff", rel)
    rec("sum_ratio", float(G.sum() / ref.sum()))
    # 1/3600 deg is not representable in binary, so GDAL's overlap weights are 1 +- O(1e-13):
    # 1e-9 relative is ~1e4 of that, while a wrong kernel (an overview, an average) is O(1) off
    check("gdal_sum_eq_blocksum", rel < 1e-9,
          "max|gdalwarp -r sum - numpy| / block |sum| = %.3e over 3,628,800 cells (abs %.3e on values "
          "to %.0f)  want < 1e-9" % (rel, R["sum_max_abs_diff"], float(np.abs(ref).max())))
    check("sum_conserves_total", abs(R["sum_ratio"] - 1.0) < 1e-9,
          "sum(out)/sum(in) = %.12f  want 1 to 1e-9 (total %.6e m)" % (R["sum_ratio"], ref.sum()))
    samples = {"sum10": G}

    # ---- identity 2: -r near -et 0 == an independent pyproj inverse of every output pixel --
    near = os.path.join(WORK, "near_utm13_30m.tif")
    xr0, yr0 = ut.c, ut.f
    p = run("warp_near", ["gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:32613",
                          "-te", repr(xr0), repr(yr0 - 30.0 * H), repr(xr0 + 30.0 * W), repr(yr0),
                          "-ts", str(W), str(H), "-r", "near", "-et", "0", "-multi",
                          "-wo", "NUM_THREADS=%d" % NT, "-wm", "1024", "-dstnodata", "-32768",
                          "-ot", "Float32"] + co + [vrt, near])
    if p.returncode:
        FAIL.append("warp_near")
        return
    inv = Transformer.from_crs("EPSG:32613", "EPSG:4326", always_xy=True)
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:32613", always_xy=True)
    vx0, vy0, vres = vt.c, vt.f, vt.a
    VH, VW = vshape
    n_valid = n_exact = n_nbr_fail = n_cover = n_edge = 0
    rt_max = 0.0
    cols = np.arange(W)
    t = time.time()
    CH = 256
    with rasterio.open(near) as d:
        for ci, r0 in enumerate(range(0, H, CH)):
            r1 = min(H, r0 + CH)
            out = d.read(1, window=Window(0, r0, W, r1 - r0))
            rr, cc = np.meshgrid(np.arange(r0, r1), cols, indexing="ij")
            x = xr0 + (cc + 0.5) * 30.0
            y = yr0 - (rr + 0.5) * 30.0
            lon, lat = inv.transform(x, y)
            lon, lat = np.asarray(lon), np.asarray(lat)
            if ci % 8 == 0:
                x2, y2 = fwd.transform(lon, lat)
                rt_max = max(rt_max, float(np.abs(np.asarray(x2) - x).max()), float(np.abs(np.asarray(y2) - y).max()))
            fc = (lon - vx0) / vres
            fr = (vy0 - lat) / vres
            ic, ir = np.floor(fc).astype(np.int64), np.floor(fr).astype(np.int64)
            inside = (ic >= 0) & (ic < VW) & (ir >= 0) & (ir < VH)
            # a pixel centre within 1e-6 px of the mosaic's outer edge may land either side
            edge = (np.minimum(np.abs(fc), np.abs(fc - VW)) < 1e-6) | (np.minimum(np.abs(fr), np.abs(fr - VH)) < 1e-6)
            ov = out != -32768
            n_edge += int(edge.sum())
            n_cover += int(((ov != inside) & ~edge).sum())
            sel = ov & inside
            n_valid += int(sel.sum())
            icc, irr = np.clip(ic, 0, VW - 1), np.clip(ir, 0, VH - 1)
            hit = M[irr, icc] == out
            n_exact += int((hit & sel).sum())
            # the remainder must equal a 3x3 neighbour: a pixel centre on a source pixel edge
            # is a tie GDAL and floor() may break differently, and nothing else is allowed
            miss = sel & ~hit
            if miss.any():
                mr, mc, mv = irr[miss], icc[miss], out[miss]
                found = np.zeros(mv.shape, bool)
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        found |= M[np.clip(mr + dr, 0, VH - 1), np.clip(mc + dc, 0, VW - 1)] == mv
                n_nbr_fail += int((~found).sum())
    rec("t_check_near", round(time.time() - t, 2))
    rec("near_valid", n_valid)
    rec("near_exact", n_exact)
    rec("near_neighbour_fail", n_nbr_fail)
    rec("near_coverage_mismatch", n_cover)
    rec("roundtrip_max_m", rt_max)
    check("near_coverage", n_cover == 0 and n_valid > 0,
          "%d px valid exactly where the inverse lands inside the mosaic, %d disagree (%d on the edge, "
          "excluded)  want 0" % (n_valid, n_cover, n_edge))
    check("near_is_source_pixel", n_nbr_fail == 0,
          "%d of %d px equal the source pixel pyproj predicts; %d equal none of its 3x3  want 0" %
          (n_exact, n_valid, n_nbr_fail))
    obs("near_exact_fraction", "%.9f of valid px equal the floor() pixel exactly; the rest are ties" %
        (n_exact / max(n_valid, 1)))
    check("reproject_roundtrip", rt_max < 1e-6,
          "32613 -> 4326 -> 32613 over every 8th 256-row chunk: %.2e m  want < 1e-6" % rt_max)

    # ---- identity 3: gdaldem slope == Horn's formula in numpy -----------------------------
    t = time.time()
    s_max = zt_max = 0.0
    n_cmp = n_nod = 0
    with rasterio.open(dem) as dd, rasterio.open(slope) as sd:
        snod = sd.nodata
        for r0 in range(1, H - 1, CH):
            r1 = min(H - 1, r0 + CH)
            z = dd.read(1, window=Window(0, r0 - 1, W, r1 - r0 + 2)).astype(np.float64)
            g = sd.read(1, window=Window(0, r0, W, r1 - r0)).astype(np.float64)[:, 1:-1]
            zv = z != -32768
            v9 = np.ones((r1 - r0, W - 2), bool)
            for dr in (0, 1, 2):
                for dc in (0, 1, 2):
                    v9 &= zv[dr:dr + r1 - r0, dc:dc + W - 2]
            a, bb, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
            dl, f = z[1:-1, :-2], z[1:-1, 2:]
            gg, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
            dzdx = ((c + 2 * f + i) - (a + 2 * dl + gg)) / (8 * 30.0)
            dzdy = ((gg + 2 * h + i) - (a + 2 * bb + c)) / (8 * 30.0)
            horn = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
            zt = np.degrees(np.arctan(np.hypot((f - dl) / 60.0, (h - bb) / 60.0)))
            gv = g != snod
            n_nod += int((gv != v9).sum())
            sel = v9 & gv
            n_cmp += int(sel.sum())
            if sel.any():
                s_max = max(s_max, float(np.abs(g[sel] - horn[sel]).max()))
                zt_max = max(zt_max, float(np.abs(g[sel] - zt[sel]).max()))
    rec("t_check_slope", round(time.time() - t, 2))
    rec("slope_max_diff_deg", s_max)
    rec("slope_zt_control_deg", zt_max)
    rec("slope_cells", n_cmp)
    rec("slope_nodata_mismatch", n_nod)
    # float32 elevations near 4,400 m carry ~5e-4 m ulp; six float32 adds in Horn's stencil over a
    # 240 m baseline is <= ~1.4e-3 deg worst case if GDAL works in float32, so 5e-3 deg; a different
    # stencil (the control) is degrees off in mountains
    check("slope_eq_horn", n_cmp > 0 and s_max < 5e-3,
          "max|gdaldem slope - Horn (numpy, float64)| = %.3e deg over %d interior px  want < 5e-3" % (s_max, n_cmp))
    check("slope_nodata_pattern", n_nod == 0,
          "%d interior px where gdaldem's nodata disagrees with 'all 9 neighbours valid'  want 0" % n_nod)
    check("slope_control_has_teeth", zt_max > 5e-2,
          "Zevenbergen-Thorne instead of Horn misses by %.2f deg  want > 5e-2 (10x the bound)" % zt_max)

    # ---- cross-chip material: pixel digests and a fixed sample of every product -------------
    t = time.time()
    for name, path in (("dem_utm", dem), ("near_utm", near), ("slope", slope), ("hillshade", hs)):
        with rasterio.open(path) as d:
            a = d.read(1)
        rec("sha_" + name, digest(a))
        samples[name] = a.ravel()[::1009].copy()
        del a
    rec("sha_sum10", digest(G))
    np.savez_compressed(os.path.join(OUT, "samples.npz"), **samples)
    rec("t_digests", round(time.time() - t, 2))
    for k in ("mosaic", "sum10", "dem_utm", "near_utm", "slope", "hillshade"):
        obs("sha256_" + k, R["sha_" + k])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 -- record, then fail the task below
        import traceback
        traceback.print_exc()
        FAIL.append("exception: %s" % type(e).__name__)
    rec("t_real_py", round(time.time() - T_START, 2))
    rec("failed", FAIL)
    json.dump(R, open(os.path.join(OUT, "results.json"), "w"), indent=1)
    open(os.path.join(OUT, "smoke-check.txt"), "w").write("\n".join(LINES) + "\n")
    print(json.dumps(R, indent=1), flush=True)
    if FAIL:
        print("FAILED: " + ", ".join(FAIL), flush=True)
        sys.exit(1)
    print("GEOSPATIAL REAL OK", flush=True)
