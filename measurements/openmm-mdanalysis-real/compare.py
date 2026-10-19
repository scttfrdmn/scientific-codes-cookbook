"""Fixed point or path: each chip's numbers against Graviton4's, at full precision.
   python3 compare.py <graviton4 dir> <other dir> ...
"""
import json
import os
import sys

import numpy as np


def load(d):
    r = json.load(open(os.path.join(d, "results.json")))
    e = {S: np.loadtxt(os.path.join(d, "energies-%s.tsv" % S), skiprows=1) for S in ("JAC", "STMV")}
    return r, e


base, be = load(sys.argv[1])
for d in sys.argv[2:]:
    r, e = load(d)
    print("== %s vs %s" % (d, sys.argv[1]))
    for S in ("JAC", "STMV"):
        a, b = base["systems"][S], r["systems"][S]
        for key in ("e0_ref", "e0_cpu"):
            for k, v in a[key].items():
                w = b[key][k]
                print("%-5s %-7s %-22s %.10f %.10f  rel %.1e  %s" % (S, key, k, v, w, abs(v - w) / abs(v),
                                                                   "identical" if v == w else ""))
        for k, v in a["e0_nb_split"].items():
            w = b["e0_nb_split"][k]
            print("%-5s split   %-22s %.10f %.10f  rel %.1e  %s" % (S, k, v, w, abs(v - w) / abs(v), "identical" if v == w else ""))
        x, y = be[S], e[S]
        for i in (1, 2, len(x) - 1):
            print("%-5s path    step %-17d Etot %.4f %.4f  (d %+.4f)  T-ish KE %.2f %.2f" % (
                S, int(x[i, 0]), x[i, 4], y[i, 4], y[i, 4] - x[i, 4], x[i, 2], y[i, 2]))
        print("%-5s step-0 Etot identical: %s; first sample that differs: step %s" % (
            S, x[0, 4] == y[0, 4], next((int(x[i, 0]) for i in range(len(x)) if x[i, 4] != y[i, 4]), "none")))
