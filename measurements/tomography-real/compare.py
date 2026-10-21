#!/usr/bin/env python3
"""Compare two tomography-real runs (two chips): which recorded values are identical, which moved.

    python3 -I compare.py <run A r1 dir> <run B r1 dir>
"""
import glob
import os
import sys

import numpy as np

A, B = sys.argv[1], sys.argv[2]
SKIP = ("phase_", "time_", "wall_s", "ru_maxrss", "astra_r")   # timings; astra rows handled below


def score(d):
    out = {}
    for line in open(os.path.join(d, "score.tsv")):
        k, _, v = line.rstrip("\n").partition("\t")
        out[k] = v
    return out


sa, sb = score(A), score(B)
same, diff = [], []
for k in sa:
    if k.startswith(SKIP) or "_time_" in k:
        continue
    (same if sa[k] == sb.get(k) else diff).append(k)
print("score.tsv: %d values identical, %d differ" % (len(same), len(diff)))
for k in diff:
    print("  %-48s %s | %s" % (k, sa[k][:70], sb.get(k, "")[:70]))
for k in sorted(sa):
    if k.startswith("astra_r") and "time" not in k and sa[k] != sb.get(k):
        print("  %-48s %s | %s" % (k, sa[k][:70], sb.get(k, "")[:70]))


def table(d):
    rows = [ln.rstrip("\n").split("\t") for ln in open(os.path.join(d, "slices.tsv"))]
    return rows[0], {r[0]: r for r in rows[1:]}


ha, ta = table(A)
_, tb = table(B)
print("slices.tsv: %d rows A, %d rows B" % (len(ta), len(tb)))
hi = ha.index("sha256")
print("  slices byte-identical across the two: %d of %d" % (sum(ta[r][hi] == tb[r][hi] for r in ta if r in tb), len(ta)))
for col in ("corr_published", "frc_min_to_half_nyquist", "frc_cross_1_7", "mean", "std"):
    i = ha.index(col)
    xa = np.array([float(ta[r][i]) for r in ta if r in tb])
    xb = np.array([float(tb[r][i]) for r in ta if r in tb])
    ok = np.isfinite(xa) & np.isfinite(xb)
    d = np.abs(xa[ok] - xb[ok])
    print("  %-26s identical %d of %d, max |diff| %.3e" % (col, int((d == 0).sum()), int(ok.sum()), d.max() if d.size else 0))
for fa in sorted(glob.glob(os.path.join(A, "*.npy"))):
    fb = os.path.join(B, os.path.basename(fa))
    if not os.path.exists(fb):
        continue
    x, y = np.load(fa), np.load(fb)
    print("  %-16s identical %s, max |diff| / max|x| %.3e" % (os.path.basename(fa), bool(np.array_equal(x, y)),
                                                           float(np.abs(x - y).max() / np.abs(x).max())))
