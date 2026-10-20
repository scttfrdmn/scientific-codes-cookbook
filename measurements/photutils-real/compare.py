"""Compare the three chips' photutils-real outputs, cell by cell (runs on AWS: compare.task.json).

    python3 -u compare.py <dir with arm64/ x86_64/ amd/, each holding cells.tar + results.json>
"""
import json
import os
import sys
import tarfile

import numpy as np

D = sys.argv[1]
CH = ["arm64", "x86_64", "amd"]
for c in CH:
    with tarfile.open(os.path.join(D, c, "cells.tar")) as t:
        t.extractall(os.path.join(D, c), filter="data")
res = {c: json.load(open(os.path.join(D, c, "results.json"))) for c in CH}
cells = sorted(res["arm64"]["hash"])
out = []
for a, b in (("x86_64", "amd"), ("arm64", "x86_64"), ("arm64", "amd")):
    same_hash = {k: sum(res[a]["hash"][c][k] == res[b]["hash"][c][k] for c in cells) for k in res[a]["hash"][cells[0]]}
    out.append("%s vs %s: cells with bit-identical output (of %d): %s" % (a, b, len(cells), same_hash))
    nsame = 0
    worst = {"x": 0.0, "y": 0.0, "flux": 0.0, "aper": 0.0}
    ndiff_area = 0
    nsrc_diff = []
    for c in cells:
        A = np.load(os.path.join(D, a, "cells", c + ".npz"))
        B = np.load(os.path.join(D, b, "cells", c + ".npz"))
        if len(A["x"]) != len(B["x"]):
            nsrc_diff.append("%s:%d/%d" % (c, len(A["x"]), len(B["x"])))
        else:
            nsame += 1
            ndiff_area += int((A["area"] != B["area"]).sum())
            for k in ("x", "y"):
                worst[k] = max(worst[k], float(np.nanmax(np.abs(A[k] - B[k]))) if len(A[k]) else 0.0)
            f = np.abs(A["flux"] - B["flux"]) / np.maximum(np.abs(A["flux"]), 1e-300)
            worst["flux"] = max(worst["flux"], float(np.nanmax(f)) if len(f) else 0.0)
        g = np.abs(A["aper"] - B["aper"]) / np.maximum(np.abs(A["aper"]), 1e-300)
        worst["aper"] = max(worst["aper"], float(np.nanmax(g)))
    out.append("  same source count in %d/%d cells%s; areas differ in %d sources; max |dx| %.2e |dy| %.2e px, max rel flux %.2e, max rel aperture flux %.2e"
               % (nsame, len(cells), (" (differ: " + " ".join(nsrc_diff[:10]) + ")") if nsrc_diff else "", ndiff_area,
                  worst["x"], worst["y"], worst["flux"], worst["aper"]))
    for k in ("sources", "parents"):
        out.append("  %s: %d vs %d" % (k, res[a][k], res[b][k]))
for c in CH:
    out.append("%s check.txt verdict: %s" % (c, open(os.path.join(D, c, "check.txt")).read().strip().splitlines()[-1]))
open("/tmp/out/crosschip.txt", "w").write("\n".join(out) + "\n")
print("\n".join(out))
print("COMPARE DONE")
