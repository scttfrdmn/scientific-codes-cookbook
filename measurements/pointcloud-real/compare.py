#!/usr/bin/env python3
"""Across chips and across runs: what of PDAL's output is a fixed point, and what is a path.

    python3 -u compare.py <dtm dir> <tile>

1. The three chips' whole-project DTMs (dtm-<chip>.tif: the mean of ground Z per 2 m cell),
   compared cell by cell.
2. One tile's pipeline A run twice on this box: are the LAZ bytes the same, and is the point SET
   the same (every point's full record, sorted)? Same set with different bytes means the order
   readers.ept emits points in is what moved.
"""
import hashlib
import json
import subprocess
import sys

import numpy as np
import rasterio
import laspy

d, tile = sys.argv[1], sys.argv[2]
ok = True
arrs = {}
for c in ("arm64", "x86_64", "amd"):
    with rasterio.open("%s/dtm-%s.tif" % (d, c)) as r:
        arrs[c] = r.read(1)
        print("%-7s DTM %s, %d cells with ground, sha256 %s" % (c, arrs[c].shape, int((arrs[c] != -9999).sum()),
                                                               hashlib.sha256(arrs[c].tobytes()).hexdigest()[:16]))
for c in ("x86_64", "amd"):
    a, b = arrs["arm64"], arrs[c]
    same_shape = a.shape == b.shape
    v = (a != -9999) & (b != -9999) if same_shape else None
    nd = int((a != b).sum()) if same_shape else -1
    mx = float(np.abs(a[v].astype(np.float64) - b[v]).max()) if same_shape and v.any() else float("nan")
    same_mask = bool(np.array_equal(a == -9999, b == -9999)) if same_shape else False
    print("%-7s vs arm64: same shape %s, same ground mask %s, %d cells differ, max |d| %.3g m" % (c, same_shape, same_mask, nd, mx))
    ok &= same_shape and same_mask

x0, x1, y0, y1 = [float(v) for v in tile.split(",")]
bounds = "([%.3f, %.3f], [%.3f, %.3f])" % (x0, x1, y0, y1)
url = "https://usgs-lidar-public.s3.us-west-2.amazonaws.com/CA_LassenNP_2020/ept.json"
recs = []
for k in range(2):
    p = [{"type": "readers.ept", "filename": url, "bounds": bounds},
         {"type": "writers.las", "filename": "/var/tmp/rep%d.laz" % k, "compression": "true", "minor_version": 4, "dataformat_id": 6,
          "scale_x": 0.01, "scale_y": 0.01, "scale_z": 0.01, "offset_x": -13513374, "offset_y": 4942220, "offset_z": 1909}]
    json.dump(p, open("/var/tmp/rep%d.json" % k, "w"))
    subprocess.run(["pdal", "pipeline", "--stream", "/var/tmp/rep%d.json" % k], check=True)
    las = laspy.read("/var/tmp/rep%d.laz" % k)
    raw = np.asarray(las.points.array)
    srt = np.sort(raw.view(np.dtype((np.void, raw.dtype.itemsize))).ravel())
    recs.append((hashlib.sha256(open("/var/tmp/rep%d.laz" % k, "rb").read()).hexdigest(), len(raw),
                 hashlib.sha256(raw.tobytes()).hexdigest(), hashlib.sha256(srt.tobytes()).hexdigest()))
    print("run %d: %d points, LAZ sha256 %s, points-in-order %s, sorted point set %s" % (k, recs[-1][1], recs[-1][0][:16], recs[-1][2][:16], recs[-1][3][:16]))
print("same LAZ bytes: %s; same order: %s; same point set: %s" % (recs[0][0] == recs[1][0], recs[0][2] == recs[1][2], recs[0][3] == recs[1][3]))
ok &= recs[0][3] == recs[1][3]
print("COMPARE OK" if ok else "COMPARE FAILED")
sys.exit(0 if ok else 1)
