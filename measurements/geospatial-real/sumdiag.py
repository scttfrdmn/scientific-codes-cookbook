#!/usr/bin/env python3
"""Which gdalwarp -r sum settings reproduce the block sums exactly, on this chip.

The same exact-grid 10x aggregation as real.py, with one setting changed per variant, each
scored against the numpy block reduction. Writes /tmp/out/sumdiag.json.
"""
import json
import os
import subprocess
import time

import numpy as np
import rasterio

WORK, OUT = "/var/tmp/work", "/tmp/out"
os.makedirs(WORK, exist_ok=True)
os.makedirs(OUT, exist_ok=True)
tiles = sorted(f for f in os.listdir("/tmp") if f.startswith("Copernicus_DSM_COG_10_") and f.endswith("_DEM.tif"))
vrt = os.path.join(WORK, "colorado.vrt")
subprocess.run(["gdalbuildvrt", "-overwrite", vrt] + ["/tmp/" + t for t in tiles], check=True)
with rasterio.open(vrt) as ds:
    vt, sh = ds.transform, ds.shape
    M = ds.read(1).astype(np.float64)
ref = M.reshape(1440, 10, 2520, 10).sum(axis=(1, 3))
del M
te = [repr(vt.c), repr(vt.f + vt.e * sh[0]), repr(vt.c + vt.a * sh[1]), repr(vt.f)]
V = {
    "base": ["-multi", "-wo", "NUM_THREADS=16", "-wm", "1024"],
    "base_repeat": ["-multi", "-wo", "NUM_THREADS=16", "-wm", "1024"],
    "et0": ["-et", "0", "-multi", "-wo", "NUM_THREADS=16", "-wm", "1024"],
    "threads1": ["-wm", "1024"],
    "et0_threads1": ["-et", "0", "-wm", "1024"],
    "default_wm": ["-multi", "-wo", "NUM_THREADS=16"],
}
# Set 2 (SUMDIAG_SET=2) asks whether the affine fast path or the polygon-clipping path is the one
# that loses weight: GDAL_WARP_USE_AFFINE_OPTIMIZATION=NO forces the latter.
NOAFF = {"GDAL_WARP_USE_AFFINE_OPTIMIZATION": "NO"}
ENV = {}
if os.environ.get("SUMDIAG_SET") == "2":
    V = {"base": V["base"], "et0": V["et0"], "base_affine_off": V["base"], "et0_affine_off": V["et0"]}
    ENV = {"base_affine_off": NOAFF, "et0_affine_off": NOAFF}
# Set 3 (SUMDIAG_SET=3) tests the cause: a geographic source makes GDAL insert a CENTER_LONG
# reprojection even when source and target CRS are the same (gdaltransformer.cpp, 3.12.4 ~l.2689),
# which disables GWKSumPreserving's affine fast path. -to INSERT_CENTER_LONG=FALSE removes it.
if os.environ.get("SUMDIAG_SET") == "3":
    NOCL = ["-to", "INSERT_CENTER_LONG=FALSE"]
    V = {"base": V["base"], "base_no_center_long": V["base"] + NOCL, "et0_no_center_long": V["et0"] + NOCL}
res = {"build": subprocess.run(["gdalinfo", "--build"], capture_output=True, text=True).stdout}
for k, extra in V.items():
    out = os.path.join(WORK, "s_%s.tif" % k)
    t = time.time()
    p = subprocess.run(["gdalwarp", "-q", "-overwrite", "-r", "sum", "-ovr", "NONE", "-te"] + te +
                       ["-ts", "2520", "1440", "-ot", "Float64"] + extra + [vrt, out], capture_output=True, text=True,
                       env=dict(os.environ, **ENV.get(k, {})))
    el = time.time() - t
    if p.returncode:
        res[k] = {"rc": p.returncode, "stderr": p.stderr[-2000:]}
        continue
    with rasterio.open(out) as d:
        g = d.read(1)
    rel = np.abs(g - ref) / ref
    res[k] = {"t": round(el, 2), "ratio": float(g.sum() / ref.sum()), "max_rel": float(rel.max()),
              "cells_off_1e-9": int((rel > 1e-9).sum()), "sha": __import__("hashlib").sha256(g.tobytes()).hexdigest()}
    print(k, res[k], flush=True)
json.dump(res, open(os.path.join(OUT, "sumdiag.json"), "w"), indent=1)
print("SUMDIAG DONE", flush=True)
