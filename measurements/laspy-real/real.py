#!/usr/bin/env python3
"""laspy over the 128 source LAZ tiles USGS delivered for 3DEP project CA_LassenNP_2020.

    python3 -u real.py <laz dir> <work dir> <out dir> <workers>

The job is the one laspy is used for at this scale: stream every tile in chunks, split it, and
write the parts back as LAZ, keeping the header, the VLRs and the EVLRs. Here every tile is split
into ground (class 2) and everything else, so all 4.64 G points are decoded and re-encoded.

  0. provenance   each staged tile: size and MD5 against the RODA listing (src.tsv), sha256 against
                  pins.tsv (absent on the probe, which writes it)
  1. laspy        per tile, in chunks: decode, account for every raw field, write ground + rest
                  LAZ with the source's VLRs and EVLRs; re-read both outputs; re-read the source
                  with a different chunk size
  2. PDAL         reads every file laspy wrote (laz-perf, a different decoder): count, bounds,
                  class and return histograms, mean Z
  3. project      totals against the EPT that pointcloud-real decoded (same points, reprojected
                  and re-encoded by Entwine), against the EPT's sources manifest, and the VPC
  4. control      the same split written the default way: no writer.write_evlrs(), and the
                  Classification Lookup VLR left to laspy. The CRS must be lost and the lookup
                  rewritten exactly as predicted, or the preservation checks could not fail
"""
import hashlib
import json
import multiprocessing as mp
import os
import resource
import struct
import subprocess
import sys
import time

import numpy as np

CHUNK = 2_000_000          # points per chunk_iterator() chunk, the main pass
CHUNK2 = 1_234_567         # a different chunk size for the re-reads (chunk invariance)
GROUND = 2
# pointcloud-real's laspy decode of the CA_LassenNP_2020 EPT (its results/arm64/results.tsv,
# identical on all three chips there). The EPT is these same points reprojected to EPSG:3857 and
# re-encoded by Entwine; class, return number and Z (cm) pass through, X and Y do not.
EPT_POINTS = 4642900288
EPT_CLASS = {1: 2795004557, 2: 1835934506, 7: 846371, 9: 10614581, 18: 23116, 20: 477157}
EPT_RETURN = {0: 57303, 1: 3265937265, 2: 860087679, 3: 366196152, 4: 118139481, 5: 27305272,
              6: 4596795, 7: 580341}
EPT_ZSUM_CM = 953651753831395          # sum of absolute Z in 0.01 m quanta
EPT_Z_RANGE_CM = (110777, 271099)
MANIFEST_SHA = "7e8228139e5234d6e294be9b29e4f261a8d4ff4499aeccf9d2feb38aca4f9caa"   # pointcloud-real's pin
VPC_SHA = "25ef26882938f4fd485afe6dc4eb2ec97f864ab37710256309fb4d9f43896c6c"
PROBE_TILES = int(os.environ.get("LR_PROBE_TILES", "0"))
PROBE = bool(PROBE_TILES)
IN = os.environ.get("LR_IN", "/tmp")       # src.tsv, pins.tsv, manifest.json, the VPC
MPCTX = os.environ.get("LR_MP", "fork")    # "spawn" only for a local syntax run on macOS

FAILS, RESULTS, TIMES = [], [], []


def rec(k, v):
    RESULTS.append((str(k), str(v)))


def tim(k, v):
    TIMES.append((str(k), str(v)))


def check(label, ok, detail, assert_it=True):
    ok = bool(ok)
    tag = "ok  " if ok else ("FAIL" if assert_it else "note")
    print("%s %-58s %s" % (tag, label, detail), flush=True)
    if assert_it and not ok:
        FAILS.append(label)


def cpu_s():
    try:
        for line in open("/sys/fs/cgroup/cpu.stat"):
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return float("nan")


# ---------------------------------------------------------------- raw VLR/EVLR parse, no laspy
def raw_vlrs(path, payloads=None):
    """(user_id, record_id, description, len, sha256 payload) for every VLR and EVLR, from the bytes.
    With a dict for `payloads`, the VLR payloads are also put in it, keyed (user_id, record_id)."""
    with open(path, "rb") as f:
        h = f.read(375)
        hs, off, nv = struct.unpack("<HII", h[94:104])
        f.seek(hs)
        v = []
        for _ in range(nv):
            b = f.read(54)
            uid, rid, ln = b[2:18].rstrip(b"\0").decode("ascii", "replace"), *struct.unpack("<HH", b[18:22])
            desc = b[22:54].rstrip(b"\0").decode("ascii", "replace")
            data = f.read(ln)
            if payloads is not None:
                payloads[(uid, rid)] = (desc, data)
            v.append((uid, rid, desc, ln, hashlib.sha256(data).hexdigest()))
        ev = []
        if h[24] == 1 and h[25] >= 4:
            start, n_ev = struct.unpack("<QI", h[235:247])
            if n_ev:
                f.seek(start)
                for _ in range(n_ev):
                    b = f.read(60)
                    uid = b[2:18].rstrip(b"\0").decode("ascii", "replace")
                    rid, ln = struct.unpack("<HQ", b[18:28])
                    desc = b[28:60].rstrip(b"\0").decode("ascii", "replace")
                    ev.append((uid, rid, desc, ln, hashlib.sha256(f.read(ln)).hexdigest()))
    return v, ev


def no_laszip(vl):
    return [x for x in vl if not (x[0] == "laszip encoded" and x[1] == 22204)]


# ---------------------------------------------------------------- provenance
def hash_job(path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            sha.update(b)
            md5.update(b)
    return os.path.basename(path), os.path.getsize(path), sha.hexdigest(), md5.hexdigest()


def etag_ok(path, size, etag):
    """A single-part ETag is the MD5; a multipart one is MD5(part MD5s)-N for some part size."""
    etag = etag.strip('"')
    if "-" not in etag:
        return None          # checked against the whole-file md5 by the caller
    n = int(etag.split("-")[1])
    for mib in sorted({-(-size // n // (1 << 20)) + d for d in (0, 1)} | {8, 16, 32, 64}):
        ps = mib << 20
        if -(-size // ps) != n:
            continue
        acc = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                b = f.read(ps)
                if not b:
                    break
                acc.update(hashlib.md5(b).digest())
        if "%s-%d" % (acc.hexdigest(), n) == etag:
            return mib
    return False


# ---------------------------------------------------------------- the laspy job
def acc_new():
    return {"n": 0, "cls": np.zeros(256, np.int64), "ret": np.zeros(16, np.int64),
            "nret": np.zeros(16, np.int64), "sums": {}, "min": None, "max": None,
            "zsum": 0, "sha": hashlib.sha256()}


def acc_add(a, ch):
    arr = ch.array
    n = len(arr)
    if n == 0:
        return
    a["n"] += n
    a["cls"] += np.bincount(arr["classification"], minlength=256)
    a["ret"] += np.bincount(np.asarray(ch.return_number, dtype=np.int64), minlength=16)
    a["nret"] += np.bincount(np.asarray(ch.number_of_returns, dtype=np.int64), minlength=16)
    for f in arr.dtype.names:            # every raw field, as unsigned 64-bit, mod 2^64
        col = arr[f]
        u = col.view(np.uint64) if col.dtype.itemsize == 8 else col.astype(np.int64).view(np.uint64)
        a["sums"][f] = (a["sums"].get(f, 0) + int(np.sum(u, dtype=np.uint64))) & ((1 << 64) - 1)
    mn = [int(arr[c].min()) for c in "XYZ"]
    mx = [int(arr[c].max()) for c in "XYZ"]
    a["min"] = mn if a["min"] is None else [min(p, q) for p, q in zip(a["min"], mn)]
    a["max"] = mx if a["max"] is None else [max(p, q) for p, q in zip(a["max"], mx)]
    a["zsum"] += int(arr["Z"].astype(np.int64).sum())
    a["sha"].update(arr.tobytes())


def acc_out(a):
    return {"n": a["n"], "cls": a["cls"], "ret": a["ret"], "nret": a["nret"], "sums": a["sums"],
            "min": a["min"], "max": a["max"], "zsum": a["zsum"], "sha": a["sha"].hexdigest()}


def hdr_of(h):
    return {"version": str(h.version), "pdrf": int(h.point_format.id), "count": int(h.point_count),
            "scales": [float(v) for v in h.scales], "offsets": [float(v) for v in h.offsets],
            "mins": [float(v) for v in h.mins], "maxs": [float(v) for v in h.maxs],
            "by_return": [int(v) for v in h.number_of_points_by_return],
            "global_encoding": int(h.global_encoding.value),
            "system_id": h.system_identifier, "software": h.generating_software,
            "crs": (lambda c: c.to_wkt() if c is not None else None)(h.parse_crs())}


def read_acc(path, chunk):
    import laspy
    a = acc_new()
    with laspy.open(path, laz_backend=laspy.LazBackend.Lazrs) as r:
        h = hdr_of(r.header)
        for ch in r.chunk_iterator(chunk):
            acc_add(a, ch)
    return h, acc_out(a)


def split_write(src, wd, stem, careful=True):
    """Stream src in chunks, write ground + rest as LAZ. Returns the source and per-part accounts.

    careful=True is what it takes to keep the metadata: write the EVLRs explicitly (the streaming
    writer drops them on close), and put the Classification Lookup VLR back as its raw bytes
    (laspy parses it into a dict of descriptions keeping only [A-Za-z0-9 ], and writes that back).
    careful=False is the default call, run as the control."""
    import copy
    import laspy
    t0 = time.time()
    s, g, o = acc_new(), acc_new(), acc_new()
    paths = {"ground": os.path.join(wd, stem + ".ground.laz"), "rest": os.path.join(wd, stem + ".rest.laz")}
    keep_evlrs = careful
    with laspy.open(src, laz_backend=laspy.LazBackend.Lazrs) as r:
        h = r.header
        hs = hdr_of(h)
        if careful:
            raw = {}
            raw_vlrs(src, raw)
            h = copy.deepcopy(h)
            for i, v in enumerate(h.vlrs):
                if (v.user_id, v.record_id) == ("LASF_Spec", 0) and ("LASF_Spec", 0) in raw:
                    desc, data = raw[("LASF_Spec", 0)]
                    h.vlrs[i] = laspy.VLR("LASF_Spec", 0, desc, data)
        wg = laspy.open(paths["ground"], mode="w", header=h, do_compress=True, laz_backend=laspy.LazBackend.Lazrs)
        wo = laspy.open(paths["rest"], mode="w", header=h, do_compress=True, laz_backend=laspy.LazBackend.Lazrs)
        for ch in r.chunk_iterator(CHUNK):
            acc_add(s, ch)
            m = ch.array["classification"] == GROUND
            pg, po = ch[m], ch[~m]
            acc_add(g, pg)
            acc_add(o, po)
            wg.write_points(pg)
            wo.write_points(po)
        for w in (wg, wo):
            if keep_evlrs and h.evlrs:
                w.write_evlrs(h.evlrs)       # the streaming writer does not write them on close
            w.close()
    return hs, acc_out(s), acc_out(g), acc_out(o), paths, time.time() - t0


def tile_job(args):
    src, wd = args
    stem = os.path.basename(src)[:-4]
    t0 = time.time()
    hs, s, g, o, paths, t_split = split_write(src, wd, stem)
    t1 = time.time()
    outs = {}
    for part, p in paths.items():
        h, a = read_acc(p, CHUNK2)
        v, ev = raw_vlrs(p)
        outs[part] = {"hdr": h, "acc": a, "vlrs": v, "evlrs": ev, "bytes": os.path.getsize(p),
                      "file_sha": hash_job(p)[2]}
    t2 = time.time()
    h2, s2 = read_acc(src, CHUNK2)          # the source again, a different chunk size
    t3 = time.time()
    v, ev = raw_vlrs(src)
    return {"stem": stem, "hdr": hs, "src": s, "src2": s2, "hdr2": h2, "ground": g, "rest": o,
            "outs": outs, "vlrs": v, "evlrs": ev, "paths": paths,
            "t_split": t_split, "t_reread_out": t2 - t1, "t_reread_src": t3 - t2, "t_all": t3 - t0,
            "rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


# ---------------------------------------------------------------- the PDAL job
def pdal_job(path):
    pj = path + ".pipeline.json"
    meta = path + ".meta.json"
    with open(pj, "w") as f:
        json.dump([{"type": "readers.las", "filename": path},
                   {"type": "filters.stats", "dimensions": "X,Y,Z,Classification,ReturnNumber",
                    "count": "Classification,ReturnNumber"}], f)
    t0 = time.time()
    try:
        with open(path + ".pdal.log", "wb") as lf:
            p = subprocess.Popen(["pdal", "pipeline", "--stream", "--metadata", meta, pj], stdout=lf, stderr=subprocess.STDOUT)
            _, status, ru = os.wait4(p.pid, 0)
        rc, rss = os.waitstatus_to_exitcode(status), ru.ru_maxrss
    except OSError as e:
        rc, rss = "%s: %s" % (type(e).__name__, e), 0
    out = {"path": path, "rc": rc, "wall": time.time() - t0, "rss_kib": rss, "st": None}
    if rc == 0:
        m = json.load(open(meta))
        st = None
        for k, v in m["stages"].items():
            if k.startswith("filters.stats"):
                st = v
        d = {}
        for x in (st or {}).get("statistic", []):
            e = {k: x[k] for k in ("count", "minimum", "maximum", "average") if k in x}
            if "counts" in x:
                e["counts"] = {int(float(c.split("/")[0])): int(c.split("/")[1]) for c in x["counts"]}
            d[x["name"]] = e
        # the printed text of each average, for the precision PDAL published it at
        mt = json.load(open(meta), parse_float=str)
        for k, v in mt["stages"].items():
            if k.startswith("filters.stats"):
                for x in v.get("statistic", []):
                    if x["name"] in d and "average" in x:
                        d[x["name"]]["average_text"] = x["average"]
        out["st"] = d
    return out


# ---------------------------------------------------------------- main
def main():
    lazdir, wd, od, workers = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    os.makedirs(wd, exist_ok=True)
    os.makedirs(od, exist_ok=True)
    T0 = time.time()
    import laspy
    import lazrs
    rec("laspy_version", laspy.__version__)
    rec("laz_backends", " ".join(str(b) for b in laspy.LazBackend.detect_available()))
    check("lazrs backend available", laspy.LazBackend.Lazrs.is_available(), str(getattr(lazrs, "__version__", "unknown")))

    # ------------------------------------------------------------ 0. provenance
    src = {}
    for line in open(os.path.join(IN, "src.tsv")):
        k, size, etag, _ = line.rstrip("\n").split("\t")
        src[k.rsplit("/", 1)[1]] = (int(size), etag)
    pins = {}
    if os.path.exists(os.path.join(IN, "pins.tsv")):
        for line in open(os.path.join(IN, "pins.tsv")):
            name, size, sha, md5 = line.rstrip("\n").split("\t")
            pins[name] = (int(size), sha, md5)
    files = sorted(os.path.join(lazdir, f) for f in os.listdir(lazdir) if f.endswith(".laz"))
    check("staged tiles == the RODA listing", sorted(map(os.path.basename, files)) == sorted(src),
          "%d staged, %d listed" % (len(files), len(src)))
    with mp.get_context(MPCTX).Pool(workers) as pool:
        hashes = pool.map(hash_job, files, chunksize=1)
    t_hash = time.time() - T0
    bad_size = [n for n, sz, _, _ in hashes if sz != src[n][0]]
    single = [(n, m) for n, _, _, m in hashes if "-" not in src[n][1].strip('"')]
    bad_md5 = [n for n, m in single if m != src[n][1].strip('"')]
    check("every tile's size == RODA's", not bad_size, "%d tiles, %d mismatches" % (len(hashes), len(bad_size)))
    check("single-part tiles: MD5 == RODA ETag", not bad_md5, "%d tiles, %d mismatches" % (len(single), len(bad_md5)))
    multi = [(p, src[os.path.basename(p)]) for p in files if "-" in src[os.path.basename(p)][1]]
    with mp.get_context(MPCTX).Pool(workers) as pool:
        mres = pool.starmap(etag_ok, [(p, s, e) for p, (s, e) in multi], chunksize=1)
    check("multipart tiles: part-MD5 ETag == RODA's", all(mres),
          "%d tiles, %d verified, part sizes %s MiB" % (len(multi), sum(1 for m in mres if m), sorted({m for m in mres if m})))
    with open(os.path.join(od, "pins.tsv"), "w") as f:
        for n, sz, sha, md5 in sorted(hashes):
            f.write("%s\t%d\t%s\t%s\n" % (n, sz, sha, md5))
    if pins:
        bad = [n for n, sz, sha, _ in hashes if pins.get(n, (0, ""))[:2] != (sz, sha)]
        check("every tile's sha256 == pins.tsv", not bad and len(pins) == len(hashes), "%d tiles, %d mismatches" % (len(hashes), len(bad)))
    else:
        check("pins.tsv written (probe; no pin to check against yet)", True, "%d tiles" % len(hashes), False)
    side = {}
    for name, want in (("manifest.json", MANIFEST_SHA), ("CA_LassenNP_2020.vpc", VPC_SHA)):
        b = open(os.path.join(IN, name), "rb").read()
        check("%s matches its sha256 pin" % name, hashlib.sha256(b).hexdigest() == want, want[:16])
        side[name] = json.loads(b)
    man = {e["path"].rsplit("/", 1)[1][:-4]: e for e in side["manifest.json"]}
    vpc = {f["id"]: f["properties"] for f in side["CA_LassenNP_2020.vpc"]["features"]}
    check("EPT manifest: 128 sources, all inserted, all from this RODA prefix",
          len(man) == 128 and all(e["inserted"] for e in man.values())
          and all(e["path"].startswith("s3://usgs-lidar/Projects/CA_LassenNP_LowerBearCreekHUC10_2020_D20/CA_LassenNP_2020/LAZ/")
                  for e in man.values()), "%d" % len(man))
    tim("wall_s_hash", "%.1f" % t_hash)

    # ------------------------------------------------------------ 1. laspy: split + write + re-read
    by_size = sorted(files, key=os.path.getsize, reverse=True)
    if PROBE:
        by_size = by_size[::max(1, len(by_size) // PROBE_TILES)][:PROBE_TILES]
    t1, c1 = time.time(), cpu_s()
    res = []
    with mp.get_context(MPCTX).Pool(workers, maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(tile_job, [(p, wd) for p in by_size], chunksize=1):
            res.append(r)
            if len(res) % 16 == 0 or len(res) == len(by_size):
                print("  laspy %d / %d tiles, %.0f s" % (len(res), len(by_size), time.time() - t1), flush=True)
    t2 = time.time()
    tim("wall_s_laspy", "%.1f" % (t2 - t1))
    tim("avg_cores_laspy", "%.2f" % ((cpu_s() - c1) / (t2 - t1)))
    tim("peak_rss_mib_laspy_worker", "%.0f" % (max(r["rss_kib"] for r in res) / 1024))
    tim("sum_s_split", "%.1f" % sum(r["t_split"] for r in res))
    tim("sum_s_reread_out", "%.1f" % sum(r["t_reread_out"] for r in res))
    tim("sum_s_reread_src", "%.1f" % sum(r["t_reread_src"] for r in res))
    res.sort(key=lambda r: r["stem"])

    F = {k: [] for k in ("count", "bounds", "byret", "vpc", "man", "manz", "chunk", "part_n", "part_sums",
                         "part_cls", "out_sha", "out_count", "out_bounds", "out_byret", "out_hdr", "out_vlr",
                         "out_evlr", "out_crs", "pdrf", "evlr_present")}
    tiles = []
    for r in res:
        h, s, st = r["hdr"], r["src"], r["stem"]
        sc, of = h["scales"], h["offsets"]
        dmin = [s["min"][q] * sc[q] + of[q] for q in range(3)]
        dmax = [s["max"][q] * sc[q] + of[q] for q in range(3)]
        if not (h["count"] == s["n"]):
            F["count"].append(st)
        if not all(abs(a - b) <= sc[q] / 2 for q, (a, b) in enumerate(zip(h["mins"], dmin))) or \
           not all(abs(a - b) <= sc[q] / 2 for q, (a, b) in enumerate(zip(h["maxs"], dmax))):
            F["bounds"].append(st)
        if h["by_return"] != [int(v) for v in s["ret"][1:16]]:
            F["byret"].append(st)
        if h["version"] != "1.4" or h["pdrf"] != 6:
            F["pdrf"].append(st)
        if not r["evlrs"]:
            F["evlr_present"].append(st)
        vp = vpc.get(st, {})
        bb = vp.get("proj:bbox", [None] * 6)
        if vp.get("pc:count") != s["n"] or not all(
                bb[k] is not None and abs(bb[k] - v) <= sc[k % 3] / 2 for k, v in enumerate(dmin + dmax)):
            F["vpc"].append(st)
        m = man.get(st, {})
        if m.get("points") != s["n"]:
            F["man"].append(st)
        if not m.get("bounds") or abs(m["bounds"][2] - dmin[2]) > sc[2] / 2 or abs(m["bounds"][5] - dmax[2]) > sc[2] / 2:
            F["manz"].append(st)
        if r["src2"]["sha"] != s["sha"] or r["src2"]["n"] != s["n"]:
            F["chunk"].append(st)
        g, o = r["ground"], r["rest"]
        if g["n"] + o["n"] != s["n"] or g["n"] != int(s["cls"][GROUND]):
            F["part_n"].append(st)
        if any(((g["sums"].get(k, 0) + o["sums"].get(k, 0)) & ((1 << 64) - 1)) != v for k, v in s["sums"].items()):
            F["part_sums"].append(st)
        cg, co = g["cls"].copy(), o["cls"].copy()
        if cg[GROUND] != s["cls"][GROUND] or cg.sum() != cg[GROUND] or co[GROUND] != 0 or not np.array_equal(cg + co, s["cls"]):
            F["part_cls"].append(st)
        for part, a in (("ground", g), ("rest", o)):
            O = r["outs"][part]
            oh, oa = O["hdr"], O["acc"]
            if oa["sha"] != a["sha"]:
                F["out_sha"].append(st + "." + part)
            if oh["count"] != oa["n"] or oa["n"] != a["n"]:
                F["out_count"].append(st + "." + part)
            if a["n"]:
                omin = [oa["min"][q] * sc[q] + of[q] for q in range(3)]
                omax = [oa["max"][q] * sc[q] + of[q] for q in range(3)]
                if oh["mins"] != omin or oh["maxs"] != omax:
                    F["out_bounds"].append(st + "." + part)
            if oh["by_return"] != [int(v) for v in oa["ret"][1:16]]:
                F["out_byret"].append(st + "." + part)
            if any(oh[k] != h[k] for k in ("version", "pdrf", "scales", "offsets", "global_encoding", "system_id")):
                F["out_hdr"].append(st + "." + part)
            if no_laszip(O["vlrs"]) != no_laszip(r["vlrs"]):
                if not F["out_vlr"]:
                    print("  VLR diff, %s.%s\n    source  %s\n    written %s" % (st, part, r["vlrs"], O["vlrs"]), flush=True)
                F["out_vlr"].append(st + "." + part)
            if O["evlrs"] != r["evlrs"]:
                F["out_evlr"].append(st + "." + part)
            if oh["crs"] is None or oh["crs"] != h["crs"]:
                F["out_crs"].append(st + "." + part)
        tiles.append([st, s["n"], g["n"], o["n"], s["sha"], r["outs"]["ground"]["file_sha"], r["outs"]["rest"]["file_sha"],
                      r["outs"]["ground"]["bytes"], r["outs"]["rest"]["bytes"], "%.1f" % r["t_split"], "%.1f" % r["t_all"],
                      "%.0f" % (r["rss_kib"] / 1024)])
    nt, no = len(res), 2 * len(res)
    L = lambda k: "%d mismatches %s" % (len(F[k]), F[k][:3])
    check("source: LAS 1.4, point format 6", not F["pdrf"], "%d tiles; %s" % (nt, L("pdrf")))
    check("source: header point count == decoded (64-bit field)", not F["count"], "%d tiles; %s" % (nt, L("count")))
    check("source: header bounds == decoded extents, within half a quantum", not F["bounds"], "%d tiles; %s" % (nt, L("bounds")))
    check("source: header points-by-return (15) == decoded", not F["byret"], "%d tiles; %s" % (nt, L("byret")))
    check("source: carries an EVLR (the WKT CRS)", not F["evlr_present"], "%d tiles; %s" % (nt, L("evlr_present")))
    check("VPC: pc:count and proj:bbox == decoded", not F["vpc"], "%d tiles; %s" % (nt, L("vpc")))
    check("EPT sources manifest: points == decoded", not F["man"], "%d tiles; %s" % (nt, L("man")))
    check("EPT sources manifest: Z bounds == decoded", not F["manz"], "%d tiles; %s" % (nt, L("manz")))
    check("chunk invariance: record bytes identical at %d and %d" % (CHUNK, CHUNK2), not F["chunk"], "%d tiles; %s" % (nt, L("chunk")))
    check("split: ground + rest == source points; ground == class 2", not F["part_n"], "%d tiles; %s" % (nt, L("part_n")))
    check("split: every raw field summed over ground + rest == source", not F["part_sums"], "%d tiles; %s" % (nt, L("part_sums")))
    check("split: class histograms partition the source's", not F["part_cls"], "%d tiles; %s" % (nt, L("part_cls")))
    check("written LAZ decodes to the exact records written (sha256)", not F["out_sha"], "%d files; %s" % (no, L("out_sha")))
    check("written header: point count == decoded", not F["out_count"], "%d files; %s" % (no, L("out_count")))
    check("written header: bounds == decoded extents, exactly", not F["out_bounds"], "%d files; %s" % (no, L("out_bounds")))
    check("written header: points-by-return == decoded", not F["out_byret"], "%d files; %s" % (no, L("out_byret")))
    check("written header: version, PDRF, scale, offset, encoding, system id kept", not F["out_hdr"], "%d files; %s" % (no, L("out_hdr")))
    check("written VLRs == source VLRs, bytes (laszip VLR aside)", not F["out_vlr"], "%d files; %s" % (no, L("out_vlr")))
    check("written EVLRs == source EVLRs, bytes", not F["out_evlr"], "%d files; %s" % (no, L("out_evlr")))
    check("written CRS (parse_crs) == source CRS", not F["out_crs"], "%d files; %s" % (no, L("out_crs")))

    # ------------------------------------------------------------ 2. PDAL reads what laspy wrote
    outs = []
    for r in res:
        for part in ("ground", "rest"):
            outs.append((r, part, r["paths"][part]))
    t3, c3 = time.time(), cpu_s()
    with mp.get_context(MPCTX).Pool(workers) as pool:
        pres = {p["path"]: p for p in pool.imap_unordered(pdal_job, sorted((x[2] for x in outs), key=os.path.getsize, reverse=True), chunksize=1)}
    t4 = time.time()
    tim("wall_s_pdal", "%.1f" % (t4 - t3))
    tim("avg_cores_pdal", "%.2f" % ((cpu_s() - c3) / (t4 - t3)))
    tim("peak_rss_mib_pdal", "%.0f" % (max(p["rss_kib"] for p in pres.values()) / 1024))
    P = {k: [] for k in ("rc", "count", "bounds", "cls", "ret", "meanz")}
    worst, worst_txt = 0.0, ""
    for r, part, path in outs:
        p = pres[path]
        name = r["stem"] + "." + part
        a = r["outs"][part]["acc"]
        if p["rc"] != 0:
            P["rc"].append(name)
            continue
        if a["n"] == 0:
            continue
        sc, of = r["hdr"]["scales"], r["hdr"]["offsets"]
        S = p["st"]
        if any(S[d]["count"] != a["n"] for d in ("X", "Y", "Z", "Classification", "ReturnNumber")):
            P["count"].append(name)
        lmin = [a["min"][q] * sc[q] + of[q] for q in range(3)]
        lmax = [a["max"][q] * sc[q] + of[q] for q in range(3)]
        if any(abs(S[c]["minimum"] - lmin[q]) > 1e-9 or abs(S[c]["maximum"] - lmax[q]) > 1e-9 for q, c in enumerate("XYZ")):
            P["bounds"].append(name)
        if S["Classification"].get("counts") != {v: int(c) for v, c in enumerate(a["cls"]) if c}:
            P["cls"].append(name)
        if S["ReturnNumber"].get("counts") != {v: int(c) for v, c in enumerate(a["ret"]) if c}:
            P["ret"].append(name)
        # laspy's mean is exact (an integer sum); PDAL's is a double accumulation, then printed.
        # Bound set a priori, two clauses: the accumulation errs by at most (n-1)*2^-53*max|z|
        # (naive summation's bound; PDAL's running mean M += (z-M)/k damps earlier errors, so it
        # is no worse), and the metadata prints the result to a fixed number of significant
        # digits, so half a unit in its last printed digit is added. Both come from the inputs.
        n = a["n"]
        exact = (a["zsum"] * sc[2]) / n + of[2]
        txt = S["Z"].get("average_text", repr(S["Z"]["average"]))
        mant = txt.lower().split("e")[0].lstrip("-")
        exp10 = int(txt.lower().split("e")[1]) if "e" in txt.lower() else 0
        frac = len(mant.split(".")[1]) if "." in mant else 0
        half_ulp = 0.5 * 10.0 ** (exp10 - frac)
        acc_b = (n - 1) * 2.0 ** -53 * max(abs(lmin[2]), abs(lmax[2]))
        bound = acc_b + half_ulp
        d = abs(S["Z"]["average"] - exact)
        if bound and d / bound > worst:
            worst = d / bound
            worst_txt = "%s: PDAL %s, exact %.15g, |d| %.2e, accumulation %.2e + print %.2e" % (name, txt, exact, d, acc_b, half_ulp)
        if d > bound:
            P["meanz"].append(name)
    check("PDAL read every laspy-written file", not P["rc"], "%d files; %d failed %s" % (len(outs), len(P["rc"]), P["rc"][:3]))
    check("PDAL count == laspy count, every dimension", not P["count"], "%d mismatches %s" % (len(P["count"]), P["count"][:3]))
    check("PDAL min/max X Y Z == laspy", not P["bounds"], "%d mismatches %s" % (len(P["bounds"]), P["bounds"][:3]))
    check("PDAL class histogram == laspy", not P["cls"], "%d mismatches %s" % (len(P["cls"]), P["cls"][:3]))
    check("PDAL return-number histogram == laspy", not P["ret"], "%d mismatches %s" % (len(P["ret"]), P["ret"][:3]))
    check("PDAL mean Z == laspy's exact mean, within summation error", not P["meanz"],
          "%d mismatches; worst |d| / bound = %.2e (%s)" % (len(P["meanz"]), worst, worst_txt))

    # ------------------------------------------------------------ 3. project totals vs the EPT
    n = sum(r["src"]["n"] for r in res)
    cls = sum((r["src"]["cls"] for r in res), np.zeros(256, np.int64))
    ret = sum((r["src"]["ret"] for r in res), np.zeros(16, np.int64))
    zsum = sum(r["src"]["zsum"] for r in res)
    zmin = min(r["src"]["min"][2] for r in res)
    zmax = max(r["src"]["max"][2] for r in res)
    clsD = {v: int(c) for v, c in enumerate(cls) if c}
    retD = {v: int(c) for v, c in enumerate(ret) if c}
    zoff = res[0]["hdr"]["offsets"][2]
    zscale_ok = all(r["hdr"]["scales"][2] == 0.01 and r["hdr"]["offsets"][2] == 0.0 for r in res)
    check("every tile: Z scale 0.01, offset 0 (raw Z is absolute cm)", zscale_ok, "offset %s" % zoff)
    check("project points == the EPT's (pointcloud-real)", n == EPT_POINTS, "%d vs %d" % (n, EPT_POINTS), not PROBE)
    check("project class histogram == the EPT's", clsD == EPT_CLASS, json.dumps(clsD), not PROBE)
    # The EPT's nodes are LAS 1.2 point format 1, whose return number is 3 bits; the delivered
    # tiles are format 6, 4 bits. So the EPT can only hold return numbers mod 8.
    ret8 = {}
    for v, c in retD.items():
        ret8[v % 8] = ret8.get(v % 8, 0) + c
    check("no return number 0 in the delivered tiles", 0 not in retD, "returns %s" % sorted(retD))
    check("project return histogram == the EPT's, raw", retD == EPT_RETURN, json.dumps(retD), False)
    check("project return histogram mod 8 == the EPT's (PDRF 1 holds 3 bits)", ret8 == EPT_RETURN, json.dumps(ret8), not PROBE)
    check("project sum of Z (cm, exact) == the EPT's", zsum == EPT_ZSUM_CM, "%d vs %d" % (zsum, EPT_ZSUM_CM), not PROBE)
    check("project Z range == the EPT's", (zmin, zmax) == EPT_Z_RANGE_CM, "%d..%d cm" % (zmin, zmax), not PROBE)
    check("project points == sum of VPC pc:count", n == sum(v["pc:count"] for v in vpc.values()) or PROBE, "%d" % n)
    gn = sum(r["ground"]["n"] for r in res)
    rec("tiles", nt)
    rec("points", n)
    rec("ground_points", gn)
    rec("rest_points", n - gn)
    rec("class_hist", json.dumps(clsD))
    rec("return_hist", json.dumps(retD))
    rec("nreturns_hist", json.dumps({v: int(c) for v, c in enumerate(sum((r["src"]["nret"] for r in res), np.zeros(16, np.int64))) if c}))
    rec("z_sum_cm", zsum)
    rec("z_range_cm", "%d..%d" % (zmin, zmax))
    rec("source_vlrs", json.dumps(sorted({(v[0], v[1], v[2]) for r in res for v in r["vlrs"]})))
    rec("source_evlrs", json.dumps(sorted({(v[0], v[1], v[2]) for r in res for v in r["evlrs"]})))
    rec("source_crs", res[0]["hdr"]["crs"][:120] if res[0]["hdr"]["crs"] else None)
    rec("source_software", json.dumps(sorted({r["hdr"]["software"] for r in res})))
    rec("records_digest(source)", hashlib.sha256("".join(r["src"]["sha"] for r in res).encode()).hexdigest())
    rec("records_digest(written)", hashlib.sha256("".join(r["outs"][p]["acc"]["sha"] for r in res for p in ("ground", "rest")).encode()).hexdigest())
    rec("laz_digest(written files)", hashlib.sha256("".join(r["outs"][p]["file_sha"] for r in res for p in ("ground", "rest")).encode()).hexdigest())
    rec("laz_bytes(written)", sum(r["outs"][p]["bytes"] for r in res for p in ("ground", "rest")))
    tim("source_bytes", sum(os.path.getsize(p) for p in by_size))
    with open(os.path.join(od, "tiles.tsv"), "w") as f:
        f.write("tile\tpoints\tground\trest\trecords_sha256\tground_laz_sha256\trest_laz_sha256\tground_bytes\trest_bytes\tsplit_s\ttile_s\trss_mib\n")
        for t in tiles:
            f.write("\t".join(map(str, t)) + "\n")

    # ------------------------------------------------------------ 4. control: no write_evlrs()
    small = min(by_size, key=os.path.getsize)
    cd = os.path.join(wd, "control")
    os.makedirs(cd, exist_ok=True)
    hs, _, _, _, cp, _ = split_write(small, cd, "control", careful=False)
    ch, _ = read_acc(cp["rest"], CHUNK)
    sraw, craw = {}, {}
    sv, _ = raw_vlrs(small, sraw)
    cv, cev = raw_vlrs(cp["rest"], craw)
    check("control: without write_evlrs() the EVLRs and the CRS are lost",
          hs["crs"] is not None and ch["crs"] is None and not cev and (ch["global_encoding"] & 16),
          "source CRS present, written EVLRs %d, parse_crs %s, WKT bit still set %s" % (len(cev), ch["crs"], bool(ch["global_encoding"] & 16)))
    # what laspy's ClassificationLookupVlr round trip predicts: one 16-byte entry per class id,
    # last one wins, each description reduced to its [A-Za-z0-9 ] characters
    lk = sraw.get(("LASF_Spec", 0), ("", b""))[1]
    pred, changed = {}, []
    for k in range(0, len(lk) - len(lk) % 16, 16):
        d = lk[k + 1:k + 16]
        keep = bytes(c for c in d if chr(c).isalnum() or c == 32)
        if keep != d.rstrip(b"\0"):
            changed.append("%d:%s->%s" % (lk[k], d.rstrip(b"\0").decode("ascii", "replace"), keep.decode()))
        pred[lk[k]] = keep
    pred_b = b"".join(struct.pack("<B15s", c, d) for c, d in pred.items())
    got_b = craw.get(("LASF_Spec", 0), ("", None))[1]
    check("control: default round trip rewrites the class lookup exactly as predicted",
          bool(lk) and got_b == pred_b and got_b != lk,
          "%d of %d descriptions altered: %s" % (len(changed), len(pred), ", ".join(changed[:8])))
    others = lambda vl: [x for x in no_laszip(vl) if (x[0], x[1]) != ("LASF_Spec", 0)]
    check("control: every other VLR survives the default round trip", others(cv) == others(sv), "%d VLRs" % len(others(sv)))
    rec("lookup_descriptions_altered_by_default", "; ".join(changed))
    finish(od, T0)


def finish(od, T0):
    tim("wall_s_total", "%.1f" % (time.time() - T0))
    for name, rows in (("results.tsv", RESULTS), ("times.tsv", TIMES)):
        with open(os.path.join(od, name), "w") as f:
            f.write("key\tvalue\n")
            for k, v in rows:
                f.write("%s\t%s\n" % (k, v))
    for k, v in TIMES:
        print("time %s %s" % (k, v))
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        sys.exit(1)
    print("LASPY REAL OK" + (" (probe: project totals not asserted)" if PROBE else ""))


if __name__ == "__main__":
    main()
