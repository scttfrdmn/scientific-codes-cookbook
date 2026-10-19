"""Difference diag.task.json's Graviton4 and Intel runs: the unit factors of every output, to the bit,
and the 800x800 projection images pixel by pixel.
   python3 -I measurements/yt-real/diag_compare.py <dir with arm64/ and x86_64/ diag outputs>"""
import os
import sys

import numpy as np

D = sys.argv[1]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "diag.txt")
lines = []
units = {c: [l.split()[1:] for l in open(os.path.join(D, c, "diag.log")) if l.startswith("units ")]
         for c in ("arm64", "x86_64")}
same = 0
for a, b in zip(units["arm64"], units["x86_64"]):
    if a == b:
        same += 1
        continue
    for fa, fb in zip(a[1:], b[1:]):
        if fa != fb:
            lines.append("units %s  %s  Graviton4 %s  Intel %s" % (a[0], fa.split("=")[0], fa.split("=")[1],
                                                                  fb.split("=")[1]))
lines.insert(0, "unit factors bit-identical on %d of %d outputs" % (same, len(units["arm64"])))
for n in ("DD0016", "DD0034", "DD0043"):
    a, b = (np.load(os.path.join(D, c, "%s-images.npz" % n)) for c in ("arm64", "x86_64"))
    for k in ("density", "temperature"):
        x, y = a[k], b[k]
        ok = bool(np.isfinite(x).all() and np.isfinite(y).all())
        d = np.abs(x - y) / np.abs(y)
        lines.append("image %s %-11s finite %s  pixels differing %6d of %d  max rel %.3e" % (
            n, k, ok, int((x != y).sum()), x.size, float(d.max())))
for c in ("arm64", "x86_64"):
    lines += ["%s %s" % (c, l.strip()) for l in open(os.path.join(D, c, "diag.log")) if ": grids" in l]
open(OUT, "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
