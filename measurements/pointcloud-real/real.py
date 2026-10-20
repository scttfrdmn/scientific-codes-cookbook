#!/usr/bin/env python3
"""pointcloud-real: a whole USGS 3DEP project (CA_LassenNP_2020, 4.64 G points) through PDAL.

    python3 -u real.py <pins.tsv> <workdir> <outdir> <workers>

Read in place from RODA (s3://usgs-lidar-public, over HTTPS). The project is cut into 2 km tiles and
each tile is one real PDAL job, `workers` at a time:

  A  readers.ept(bounds=tile) -> filters.stats -> writers.las (LAZ 1.4, the EPT's own scale/offset)
  B  readers.las(the tile)    -> filters.stats -> filters.range(Classification[2:2])
                              -> writers.gdal (2 m DTM: min, mean, count; binmode)

Then laspy (lazrs, an independent LAZ decoder) reads every EPT node the project has, each one
checked against its sha256 pin. The checks are in check(); everything kept lands in <outdir>.
PC_PROBE_TILES / PC_PROBE_NODES limit the run to the first N tiles / nodes (a wiring probe), in
which case the whole-project identities are reported but not asserted.
"""
import concurrent.futures as cf
import hashlib
import http.client
import io
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
import urllib.parse

import numpy as np
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)   # numpy 2.5 vs rasterio 1.5: noise only

PROJECT = "CA_LassenNP_2020"
HOST = "usgs-lidar-public.s3.us-west-2.amazonaws.com"
EPT_URL = "https://%s/%s/ept.json" % (HOST, PROJECT)
TILE_M = 2000.0      # tile edge, metres (EPSG:3857, the EPT's own CRS)
RES_M = 2.0          # DTM cell, metres
PROBE_TILES = int(os.environ.get("PC_PROBE_TILES", "0"))
PROBE_NODES = int(os.environ.get("PC_PROBE_NODES", "0"))
PROBE = bool(PROBE_TILES or PROBE_NODES)

FAILS = []
RESULTS = []          # (key, value): fixed values, compared across chips
TIMES = []            # (key, value): timings and memory, not compared


def cpu_s():
    try:
        for line in open("/sys/fs/cgroup/cpu.stat"):
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return float("nan")


def rec(k, v):
    RESULTS.append((str(k), str(v)))


def tim(k, v):
    TIMES.append((str(k), str(v)))


def check(label, ok, detail, assert_it=True):
    ok = bool(ok)
    tag = "ok  " if ok else ("FAIL" if assert_it else "note")
    print("%s %-44s %s" % (tag, label, detail), flush=True)
    if assert_it and not ok:
        FAILS.append(label)


# ---------------------------------------------------------------- fetching, pinned
_conn = {}


def fetch(key):
    """GET one object over a per-process keep-alive connection; three tries."""
    pid = (os.getpid(), threading.get_ident())     # one connection per process and thread
    for attempt in range(3):
        try:
            c = _conn.get(pid)
            if c is None:
                c = _conn[pid] = http.client.HTTPSConnection(HOST, timeout=60)
            c.request("GET", "/" + urllib.parse.quote(key))
            r = c.getresponse()
            body = r.read()
            if r.status != 200:
                raise IOError("HTTP %d for %s" % (r.status, key))
            return body
        except Exception:
            _conn.pop(pid, None)
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


PINS = {}


def load_pins(path):
    for line in open(path):
        k, s, h = line.rstrip("\n").split("\t")
        PINS[k] = (int(s), h)


def fetch_pinned(key):
    body = fetch(key)
    size, sha = PINS[key]
    return body, (len(body) == size and hashlib.sha256(body).hexdigest() == sha)


# ---------------------------------------------------------------- PDAL helpers
def run(cmd, log):
    """Run a command; return (rc, wall s, ru_maxrss KiB) from wait4, so peak RSS is the kernel's."""
    t0 = time.time()
    with open(log, "ab") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
        _, status, ru = os.wait4(p.pid, 0)
    return os.waitstatus_to_exitcode(status), time.time() - t0, ru.ru_maxrss


def stats_of(meta):
    """filters.stats metadata -> {dim: {count,minimum,maximum,average}}, plus value counts."""
    st = None
    for k, v in meta["stages"].items():
        if k.startswith("filters.stats"):
            st = v
    if not st or not st.get("statistic"):
        return {"X": {"count": 0}}
    out = {}
    for s in st["statistic"]:
        d = {k: s[k] for k in ("count", "minimum", "maximum", "average") if k in s}
        if "counts" in s:
            d["counts"] = {int(float(c.split("/")[0])): int(c.split("/")[1]) for c in s["counts"]}
        out[s["name"]] = d
    return out


def tile_job(t):
    i, j, x0, x1, y0, y1, wd, ox, oy, oz = t
    name = "t%02d_%02d" % (i, j)
    laz, dtm, log = "%s/%s.laz" % (wd, name), "%s/%s.dtm.tif" % (wd, name), "%s/%s.log" % (wd, name)
    bounds = "([%.3f, %.3f], [%.3f, %.3f])" % (x0, x1, y0, y1)
    dims = "X,Y,Z,Classification,ReturnNumber"
    pa = [{"type": "readers.ept", "filename": EPT_URL, "bounds": bounds},
          {"type": "filters.stats", "dimensions": dims, "count": "Classification,ReturnNumber"},
          {"type": "writers.las", "filename": laz, "compression": "true", "minor_version": 4, "dataformat_id": 6,
           "scale_x": 0.01, "scale_y": 0.01, "scale_z": 0.01, "offset_x": ox, "offset_y": oy, "offset_z": oz}]
    pb = [{"type": "readers.las", "filename": laz},
          {"type": "filters.stats", "dimensions": dims, "count": "Classification,ReturnNumber"},
          {"type": "filters.range", "limits": "Classification[2:2]"},
          {"type": "writers.gdal", "filename": dtm, "resolution": RES_M, "binmode": True, "output_type": "min,mean,count",
           "bounds": bounds, "data_type": "float32", "nodata": -9999, "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=3,TILED=YES"}]
    r = {"name": name, "bounds": bounds}
    for tag, p in (("A", pa), ("B", pb)):
        pj, mj = "%s/%s.%s.json" % (wd, name, tag), "%s/%s.%s.meta.json" % (wd, name, tag)
        json.dump(p, open(pj, "w"))
        rc, w, rss = run(["pdal", "pipeline", "--stream", "--metadata", mj, pj], log)
        r[tag + "_rc"], r[tag + "_wall"], r[tag + "_rss_kib"] = rc, w, rss
        if rc != 0:
            r["error"] = "%s exit %d: %s" % (tag, rc, open(log, errors="replace").read()[-800:])
            return r
        r[tag] = stats_of(json.load(open(mj)))
        if tag == "A" and r["A"]["X"]["count"] == 0:
            break
    if r["A"]["X"]["count"] == 0:
        if os.path.exists(laz):
            os.remove(laz)
        return r
    # the header round trip: PDAL's own reading of the written header, and laspy's
    out = subprocess.run(["pdal", "info", "--metadata", laz], capture_output=True, text=True)
    m = json.loads(out.stdout)["metadata"]
    r["pdal_hdr"] = {k: m.get(k) for k in ("count", "minx", "maxx", "miny", "maxy", "minz", "maxz", "minor_version", "dataformat_id")}
    import laspy
    with laspy.open(laz) as f:
        h = f.header
        r["laspy_hdr"] = {"point_count": int(h.point_count), "by_return": [int(v) for v in h.number_of_points_by_return],
                          "mins": [float(v) for v in h.mins], "maxs": [float(v) for v in h.maxs],
                          "scales": [float(v) for v in h.scales], "offsets": [float(v) for v in h.offsets],
                          "version": str(h.version), "point_format": int(h.point_format.id)}
    r["laz_bytes"] = os.path.getsize(laz)
    r["laz_sha256"] = hashlib.sha256(open(laz, "rb").read()).hexdigest()
    os.remove(laz)                      # created by this task, so it may be removed; 25+ GB in total
    if "B" in r:
        import rasterio
        with rasterio.open(dtm) as d:
            mn, me, ct = d.read(1), d.read(2), d.read(3)
            r["dtm_shape"] = [int(d.height), int(d.width)]
            r["dtm_transform"] = [float(v) for v in d.transform[:6]]
        has = ct > 0
        r["dtm_count_sum"] = int(ct[has].astype(np.int64).sum())
        r["dtm_cells"] = int(has.sum())
        r["dtm_min_finite_iff_count"] = bool(np.array_equal(has, np.isfinite(mn) & (mn != -9999)))
        r["dtm_min_le_mean"] = bool(np.all(mn[has] <= me[has]))
        r["dtm_sha256"] = [hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest() for a in (mn, me, ct)]
        r["dtm_bytes"] = os.path.getsize(dtm)
    return r


# ---------------------------------------------------------------- laspy cross-decoder
def node_job(key):
    import laspy
    t0 = time.time()
    body, ok = fetch_pinned(key)
    t1 = time.time()
    las = laspy.read(io.BytesIO(body), laz_backend=laspy.LazBackend.Lazrs)
    h = las.header
    X, Y, Z = (np.asarray(las.X, dtype=np.int64), np.asarray(las.Y, dtype=np.int64), np.asarray(las.Z, dtype=np.int64))
    n = len(X)
    # each EPT node has its own offset; on the EPT's 0.01 grid it is a whole number of quanta, so
    # raw + offset/scale is the point's absolute integer coordinate, exactly
    k = [int(round(float(o_) / float(s_))) for o_, s_ in zip(h.offsets, h.scales)]
    on_grid = all(abs(k[q] * float(h.scales[q]) - float(h.offsets[q])) < 1e-6 for q in range(3))
    X, Y, Z = X + k[0], Y + k[1], Z + k[2]
    out = {"on_grid": on_grid, "key": key, "pin_ok": ok, "hdr_count": int(h.point_count), "n": n,
           "scales": [float(v) for v in h.scales], "offsets": [float(v) for v in h.offsets],
           "cls": np.bincount(np.asarray(las.classification, dtype=np.int64), minlength=256),
           "ret": np.bincount(np.asarray(las.return_number, dtype=np.int64), minlength=16),
           "sum": [int(X.sum()), int(Y.sum()), int(Z.sum())] if n else [0, 0, 0],
           "min": [int(X.min()), int(Y.min()), int(Z.min())] if n else None,
           "max": [int(X.max()), int(Y.max()), int(Z.max())] if n else None,
           "t_fetch": t1 - t0, "t_decode": time.time() - t1, "bytes": len(body)}
    return out


def main():
    pins_path, wd, od, workers = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    os.makedirs(wd, exist_ok=True)
    os.makedirs(od, exist_ok=True)
    load_pins(pins_path)
    T0 = time.time()

    # ------------------------------------------------------------ 1. metadata, pinned
    pre = PROJECT + "/"
    ept_b, ok = fetch_pinned(pre + "ept.json")
    ept = json.loads(ept_b)
    man_b, ok2 = fetch_pinned(pre + "ept-sources/manifest.json")
    man = json.loads(man_b)
    hkeys = sorted(k for k in PINS if k.startswith(pre + "ept-hierarchy/"))
    dkeys = sorted(k for k in PINS if k.startswith(pre + "ept-data/"))
    with cf.ThreadPoolExecutor(32) as ex:
        hier = list(ex.map(fetch_pinned, hkeys))
    nodes = {}
    for b, _ in hier:
        for k, v in json.loads(b).items():
            if v > 0:
                nodes[k] = v
    check("metadata matches its sha256 pins", ok and ok2 and all(o for _, o in hier),
          "ept.json, ept-sources/manifest.json and %d hierarchy files" % len(hkeys))
    N = int(ept["points"])
    sch = {d["name"]: d for d in ept["schema"]}
    ox, oy, oz = (float(sch[c]["offset"]) for c in "XYZ")
    sc = [float(sch[c]["scale"]) for c in "XYZ"]
    rec("ept_points", N)
    rec("hierarchy_nodes", len(nodes))
    rec("data_objects", len(dkeys))
    check("sum(hierarchy counts) == ept.json points", sum(nodes.values()) == N, "%d == %d" % (sum(nodes.values()), N))
    check("sum(ept-sources manifest points) == ept.json points", sum(e["points"] for e in man) == N,
          "%d tiles, %d == %d" % (len(man), sum(e["points"] for e in man), N))
    dnodes = {k.rsplit("/", 1)[1].rsplit(".", 1)[0] for k in dkeys}
    check("one ept-data object per non-empty hierarchy node", dnodes == set(nodes), "%d objects, %d nodes" % (len(dnodes), len(nodes)))

    # ------------------------------------------------------------ 2. PDAL, tile by tile
    bc = ept["boundsConforming"]
    X0 = ox + sc[0] * (math.floor((bc[0] - ox) / sc[0]) - 0.5)       # tile edges on half-quanta:
    Y0 = oy + sc[1] * (math.floor((bc[1] - oy) / sc[1]) - 0.5)       # no point can sit on one
    nx, ny = math.ceil((bc[3] - X0) / TILE_M), math.ceil((bc[4] - Y0) / TILE_M)
    tiles, skipped = [], 0
    for j in range(ny):
        for i in range(nx):
            x0, x1, y0, y1 = X0 + i * TILE_M, X0 + (i + 1) * TILE_M, Y0 + j * TILE_M, Y0 + (j + 1) * TILE_M
            if not any(e["bounds"][0] < x1 and e["bounds"][3] > x0 and e["bounds"][1] < y1 and e["bounds"][4] > y0 for e in man):
                skipped += 1
                continue
            tiles.append((i, j, x0, x1, y0, y1, wd, ox, oy, oz))
    if PROBE_TILES:
        tiles = tiles[:PROBE_TILES]
    print("grid %d x %d of %.0f m from (%.3f, %.3f); %d tiles touch a source, %d skipped; %d workers"
          % (nx, ny, TILE_M, X0, Y0, len(tiles), skipped, workers), flush=True)
    rec("grid", "%dx%d origin %.3f %.3f tile %.0f m" % (nx, ny, X0, Y0, TILE_M))
    rec("tiles_run", len(tiles))
    t2, c2 = time.time(), cpu_s()
    res = []
    with cf.ThreadPoolExecutor(workers) as ex:
        for r in ex.map(tile_job, tiles):
            res.append(r)
            if "error" in r:
                print("TILE ERROR %s %s" % (r["name"], r["error"]), flush=True)
            if len(res) % 10 == 0:
                print("  %d / %d tiles, %.0f s" % (len(res), len(tiles), time.time() - t2), flush=True)
    t3 = time.time()
    tim("wall_s_pdal", "%.1f" % (t3 - t2))
    tim("avg_cores_pdal", "%.2f" % ((cpu_s() - c2) / (t3 - t2)))
    errs = [r for r in res if "error" in r]
    check("every PDAL pipeline exited 0", not errs, "%d tiles, %d errors" % (len(res), len(errs)))
    if errs:
        json.dump(res, open(os.path.join(od, "tiles.json"), "w"), indent=1, default=str)
        finish(od, T0)
        return
    good = [r for r in res if r["A"]["X"]["count"] > 0]
    for tag in ("A", "B"):
        tim("sum_s_pdal_" + tag, "%.1f" % sum(r.get(tag + "_wall", 0) for r in res))
        tim("peak_rss_mib_pdal_" + tag, "%.0f" % (max(r.get(tag + "_rss_kib", 0) for r in res) / 1024))

    def tot(tag, dim):
        c = {}
        for r in good:
            for v, n in r[tag][dim]["counts"].items():
                c[v] = c.get(v, 0) + n
        return dict(sorted(c.items()))

    nA = sum(r["A"]["X"]["count"] for r in good)
    clsA, retA = tot("A", "Classification"), tot("A", "ReturnNumber")
    rec("pdal_points", nA)
    rec("pdal_class_hist", json.dumps(clsA))
    rec("pdal_return_hist", json.dumps(retA))
    check("sum over tiles of PDAL points == ept.json points", nA == N, "%d tiles with points, %d == %d" % (len(good), nA, N), not PROBE)
    check("PDAL class histogram sums to its point count", sum(clsA.values()) == nA, "%d classes" % len(clsA))

    # round trip, per tile: the written LAZ read back by PDAL (B), by PDAL's header, by laspy's header
    rt = {"count": 0, "hist": 0, "minmax": 0, "avg_exact": 0, "hdr_pdal": 0, "hdr_laspy": 0, "by_return": 0, "scale_offset": 0}
    worst_avg = 0.0
    for r in good:
        A, B, hp, hl = r["A"], r["B"], r["pdal_hdr"], r["laspy_hdr"]
        rt["count"] += all(B[d]["count"] == A[d]["count"] for d in A)
        rt["hist"] += B["Classification"]["counts"] == A["Classification"]["counts"] and B["ReturnNumber"]["counts"] == A["ReturnNumber"]["counts"]
        rt["minmax"] += all(B[d]["minimum"] == A[d]["minimum"] and B[d]["maximum"] == A[d]["maximum"] for d in A)
        rt["avg_exact"] += all(B[d]["average"] == A[d]["average"] for d in A)
        worst_avg = max(worst_avg, max(abs(B[d]["average"] - A[d]["average"]) / max(abs(A[d]["average"]), 1) for d in A))
        rt["hdr_pdal"] += hp["count"] == A["X"]["count"] and all(
            abs(hp[k] - A[c][s]) < 1e-6 for k, c, s in (("minx", "X", "minimum"), ("maxx", "X", "maximum"), ("miny", "Y", "minimum"),
                                                        ("maxy", "Y", "maximum"), ("minz", "Z", "minimum"), ("maxz", "Z", "maximum")))
        rt["hdr_laspy"] += hl["point_count"] == A["X"]["count"] and all(
            abs(hl["mins"][q] - A[c]["minimum"]) < 1e-6 and abs(hl["maxs"][q] - A[c]["maximum"]) < 1e-6 for q, c in enumerate("XYZ"))
        rt["by_return"] += hl["by_return"] == [A["ReturnNumber"]["counts"].get(k, 0) for k in range(1, len(hl["by_return"]) + 1)]
        rt["scale_offset"] += hl["scales"] == sc and hl["offsets"] == [ox, oy, oz] and hl["version"] == "1.4" and hl["point_format"] == 6
    g = len(good)
    check("round trip: B count == A count, every dim", rt["count"] == g, "%d / %d tiles" % (rt["count"], g))
    check("round trip: class + return histograms conserved", rt["hist"] == g, "%d / %d tiles" % (rt["hist"], g))
    check("round trip: min/max X Y Z identical", rt["minmax"] == g, "%d / %d tiles" % (rt["minmax"], g))
    check("round trip: mean X Y Z within 1e-12 relative", worst_avg <= 1e-12,
          "worst %.2e; bit-identical on %d / %d tiles" % (worst_avg, rt["avg_exact"], g))
    check("header (PDAL): count + bounds == decoded", rt["hdr_pdal"] == g, "%d / %d tiles" % (rt["hdr_pdal"], g))
    check("header (laspy): count + bounds == decoded", rt["hdr_laspy"] == g, "%d / %d tiles" % (rt["hdr_laspy"], g))
    check("header points-by-return == decoded ReturnNumber", rt["by_return"] == g, "%d / %d tiles" % (rt["by_return"], g))
    check("header: LAS 1.4, PDRF 6, the EPT's scale + offset", rt["scale_offset"] == g, "%d / %d tiles" % (rt["scale_offset"], g))
    rec("roundtrip_avg_bit_identical_tiles", rt["avg_exact"])

    # the DTM: every ground point lands in exactly one cell
    gB = sum(r["B"]["Classification"]["counts"].get(2, 0) for r in good)
    gD = sum(r["dtm_count_sum"] for r in good)
    per = sum(r["dtm_count_sum"] == r["B"]["Classification"]["counts"].get(2, 0) for r in good)
    check("DTM: sum(count band) == class-2 points, per tile", per == g, "%d / %d tiles; %d == %d" % (per, g, gD, gB))
    check("DTM: min finite exactly where count > 0", all(r["dtm_min_finite_iff_count"] for r in good), "%d tiles" % g)
    check("DTM: min <= mean in every cell", all(r["dtm_min_le_mean"] for r in good), "%d cells" % sum(r["dtm_cells"] for r in good))
    rec("ground_points", gB)
    rec("dtm_cells", sum(r["dtm_cells"] for r in good))
    hh = hashlib.sha256()
    for r in sorted(good, key=lambda r: r["name"]):
        hh.update(("%s %s %s\n" % (r["name"], r["laz_sha256"], " ".join(r["dtm_sha256"]))).encode())
    rec("tiles_digest(laz bytes + dtm arrays)", hh.hexdigest())
    rec("laz_bytes_total", sum(r["laz_bytes"] for r in good))
    zsum = sum(r["A"]["Z"]["average"] * r["A"]["Z"]["count"] for r in good)
    rec("pdal_mean_z", repr(zsum / nA))

    with open(os.path.join(od, "tiles.tsv"), "w") as f:
        f.write("tile\tpoints\tground\tdtm_cells\tlaz_bytes\tlaz_sha256\tdtm_sha256_min\tA_s\tB_s\tA_rss_mib\tB_rss_mib\n")
        for r in sorted(res, key=lambda r: r["name"]):
            if r["A"]["X"]["count"] == 0:
                f.write("%s\t0\n" % r["name"])
                continue
            f.write("%s\t%d\t%d\t%d\t%d\t%s\t%s\t%.1f\t%.1f\t%.0f\t%.0f\n" % (
                r["name"], r["A"]["X"]["count"], r["B"]["Classification"]["counts"].get(2, 0), r["dtm_cells"], r["laz_bytes"],
                r["laz_sha256"], r["dtm_sha256"][0], r["A_wall"], r["B_wall"], r["A_rss_kib"] / 1024, r["B_rss_kib"] / 1024))

    # one 2 m DTM (mean of ground Z) for the whole project, as a COG
    from osgeo import gdal
    gdal.UseExceptions()
    vrt = gdal.BuildVRT(os.path.join(wd, "dtm.vrt"), sorted("%s/%s.dtm.tif" % (wd, r["name"]) for r in good), bandList=[2])
    gdal.Translate(os.path.join(od, "dtm-2m-mean.tif"), vrt, format="COG",
                   creationOptions=["COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=IF_SAFER"])
    vrt = None
    t4, c4 = time.time(), cpu_s()
    tim("wall_s_mosaic", "%.1f" % (t4 - t3))

    # ------------------------------------------------------------ 3. laspy over every EPT node
    keys = dkeys[:PROBE_NODES] if PROBE_NODES else dkeys
    cls, ret = np.zeros(256, np.int64), np.zeros(16, np.int64)
    s3, mn, mx = [0, 0, 0], [None] * 3, [None] * 3
    bad_pin, bad_cnt, bad_so, n_l, tf, td, nb = [], [], [], 0, 0.0, 0.0, 0
    with mp.get_context("fork").Pool(workers) as pool:
        for o in pool.imap_unordered(node_job, keys, chunksize=32):
            node = o["key"].rsplit("/", 1)[1].rsplit(".", 1)[0]
            if not o["pin_ok"]:
                bad_pin.append(o["key"])
            if not (o["hdr_count"] == o["n"] == nodes.get(node)):
                bad_cnt.append(o["key"])
            if o["scales"] != sc or not o["on_grid"]:
                bad_so.append(o["key"])
            cls += o["cls"]
            ret += o["ret"]
            n_l += o["n"]
            nb += o["bytes"]
            tf += o["t_fetch"]
            td += o["t_decode"]
            for q in range(3):
                s3[q] += o["sum"][q]
                if o["min"]:
                    mn[q] = o["min"][q] if mn[q] is None else min(mn[q], o["min"][q])
                    mx[q] = o["max"][q] if mx[q] is None else max(mx[q], o["max"][q])
    t5 = time.time()
    tim("wall_s_laspy", "%.1f" % (t5 - t4))
    tim("avg_cores_laspy", "%.2f" % ((cpu_s() - c4) / (t5 - t4)))
    tim("sum_s_laspy_fetch", "%.1f" % tf)
    tim("sum_s_laspy_decode", "%.1f" % td)
    print("laspy read %d nodes, %d bytes, %d points in %.1f s" % (len(keys), nb, n_l, t5 - t4), flush=True)
    check("every EPT node matches its sha256 pin", not bad_pin, "%d nodes, %d mismatches %s" % (len(keys), len(bad_pin), bad_pin[:3]))
    check("laspy: node header count == decoded == hierarchy", not bad_cnt, "%d nodes, %d mismatches %s" % (len(keys), len(bad_cnt), bad_cnt[:3]))
    check("laspy: every node on the EPT's 0.01 grid (scale, offset)", not bad_so, "%d mismatches" % len(bad_so))
    check("laspy: sum of points == ept.json points", n_l == N, "%d == %d" % (n_l, N), not PROBE)
    clsL = {v: int(c) for v, c in enumerate(cls) if c}
    retL = {v: int(c) for v, c in enumerate(ret) if c}
    rec("laspy_points", n_l)
    rec("laspy_class_hist", json.dumps(clsL))
    rec("laspy_return_hist", json.dumps(retL))
    rec("laspy_quanta_sums", " ".join(str(v) for v in s3))
    check("PDAL (laz-perf) class histogram == laspy (lazrs)", clsL == clsA, "%d classes" % len(clsL), not PROBE)
    check("PDAL (laz-perf) return histogram == laspy (lazrs)", retL == retA, "%d returns" % len(retL), not PROBE)
    if mn[0] is not None and good:
        pm = [min(r["A"][c]["minimum"] for r in good) for c in "XYZ"]
        pM = [max(r["A"][c]["maximum"] for r in good) for c in "XYZ"]
        lm = [mn[q] * sc[q] for q in range(3)]
        lM = [mx[q] * sc[q] for q in range(3)]
        check("PDAL min/max X Y Z == laspy, same quantum", all(abs(a - b) < 1e-6 for a, b in zip(pm + pM, lm + lM)),
              "Z %.2f..%.2f vs %.2f..%.2f" % (pm[2], pM[2], lm[2], lM[2]), not PROBE)
        check("data inside ept.json boundsConforming", all(bc[q] <= lm[q] and lM[q] <= bc[q + 3] for q in range(3)),
              "Z %.2f..%.2f in %s..%s" % (lm[2], lM[2], bc[2], bc[5]))
        # laspy's mean is exact (an integer sum); PDAL's is a double accumulation over each tile.
        # A priori bound, not fitted: naive summation of n doubles errs by at most (n-1)*u*sum|z|
        # (u = 2^-53), so a tile's mean errs by at most (n-1)*u*max|z|; weight by n over the tiles.
        lz = (s3[2] / n_l) * sc[2]
        pz = zsum / nA
        bound = sum(r["A"]["Z"]["count"] * (r["A"]["Z"]["count"] - 1) * 2.0 ** -53
                    * max(abs(r["A"]["Z"]["minimum"]), abs(r["A"]["Z"]["maximum"])) for r in good) / nA
        check("PDAL mean Z == laspy's exact mean, within summation error", abs(pz - lz) <= bound,
              "%.9f vs %.9f, |d| %.2e <= %.2e" % (pz, lz, abs(pz - lz), bound), not PROBE)
        rec("laspy_mean_z_exact", repr(lz))
    finish(od, T0)


def finish(od, T0):
    tim("wall_s_total", "%.1f" % (time.time() - T0))
    with open(os.path.join(od, "results.tsv"), "w") as f:
        f.write("key\tvalue\n")
        for k, v in RESULTS:
            f.write("%s\t%s\n" % (k, v))
    with open(os.path.join(od, "times.tsv"), "w") as f:
        f.write("key\tvalue\n")
        for k, v in TIMES:
            f.write("%s\t%s\n" % (k, v))
    for k, v in TIMES:
        print("time %s %s" % (k, v))
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        sys.exit(1)
    print("POINTCLOUD REAL OK" + (" (probe: whole-project identities not asserted)" if PROBE else ""))


if __name__ == "__main__":
    main()
