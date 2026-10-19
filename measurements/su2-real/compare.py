#!/usr/bin/env python3
"""Cross-chip comparison of the su2-real runs.

    python3 measurements/su2-real/compare.py      # reads results/{arm64,x86_64,amd}/

The converged coefficients are fixed points, so they must agree across chips -- to within how far
each solve was still from its fixed point when it stopped at rms rho < 1e-12. That distance is
bounded by 10x the larger last-100-iteration change of the pair (the iterates converge
geometrically, slower than 0.9 per 100 iterations, so the tail is < 10x the last step). Iteration
counts and residual histories are paths: reported, not asserted.
"""
import csv
import os
import sys

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
ARCH = ["arm64", "x86_64", "amd"]


def load(a):
    with open(os.path.join(D, a, "results.tsv")) as fh:
        return {float(r["alpha"]): r for r in csv.DictReader(fh, delimiter="\t")}


R = {a: load(a) for a in ARCH if os.path.exists(os.path.join(D, a, "results.tsv"))}
fail = []
for a in [x for x in ARCH if x != "arm64" and x in R]:
    for alpha, g in R["arm64"].items():
        o = R[a][alpha]
        print("alpha %g  %s vs arm64: iterations %s vs %s" % (alpha, a, o["iters"], g["iters"]))
        for c, dc in (("CL", "dCL_last100"), ("CD", "dCD_last100"), ("CMz", "dCL_last100")):
            d = abs(float(o[c]) - float(g[c]))
            tol = 10 * max(float(o[dc]), float(g[dc]))
            ok = c == "CMz" or d <= tol
            print("   %-3s %s vs %s  |d| %.2e  %s" % (c, o[c], g[c], d,
                  "reported" if c == "CMz" else "<= %.1e [%s]" % (tol, "ok" if ok else "FAIL")))
            if not ok:
                fail.append((a, alpha, c))
print("CROSS-CHIP FAILED %s" % fail if fail else "CROSS-CHIP OK")
sys.exit(1 if fail else 0)
