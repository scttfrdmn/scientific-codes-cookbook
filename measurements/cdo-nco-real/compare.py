#!/usr/bin/env python3
"""Compare the three chips' runs: every result row, every data hash, and the staged arrays.

    python3 -I compare.py <scratch>/arm64 <scratch>/x86_64 <scratch>/amd

Each dir holds that chip's cdo-nco-results.tsv, g.nc, gr.nc and ann_cdo.npy.
"""
import subprocess
import sys

import numpy as np

dirs = sys.argv[1:]
names = ["Graviton4", "Intel", "AMD"]


def tsv(d):
    return dict(l.rstrip("\n").split("\t", 1) for l in open(d + "/cdo-nco-results.tsv") if "\t" in l)


def nc(path):
    """VAR_2T via ncdump at 17 significant digits, which round-trips a float64 exactly."""
    out = subprocess.run(["ncdump", "-v", "VAR_2T", "-p", "17,17", path], capture_output=True,
                         text=True, check=True).stdout
    body = out.split("VAR_2T =", 1)[1].split(";", 1)[0]
    return np.array([float(x) for x in body.replace("\n", " ").split(",")], "f8")


R = [tsv(d) for d in dirs]
print("%-52s %s" % ("result", " | ".join(names)))
for k in R[0]:
    vals = [r.get(k, "-") for r in R]
    tag = "same" if len(set(vals)) == 1 else "DIFFERS"
    print("%-52s %-7s %s" % (k, tag, " | ".join(vals)))
for f, load in (("g.nc", nc), ("gr.nc", nc), ("ann_cdo.npy", lambda p: np.load(p).reshape(-1))):
    a = load(dirs[0] + "/" + f)
    for n, d in zip(names[1:], dirs[1:]):
        b = load(d + "/" + f)
        print("%s %s vs Graviton4: bit-identical %s, max rel diff %.3e"
              % (f, n, bool(np.array_equal(a, b)), float(np.max(np.abs(a - b)) / np.max(np.abs(a)))))
