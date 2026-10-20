#!/usr/bin/env python3
"""Elementwise cross-chip comparison of the 209 thickness fields gfs.py wrote on each chip.

    compare.py <arm64 thick dir> <x86_64 thick dir> <amd thick dir>

The decoded GRIB2 values are byte-identical across chips (gfs.py's decode digest); the thickness
is a closed-form expression of them (log, exp, a 13-term trapezoid), so any difference is the
math library's last bits. This measures how many: per pair, cells that differ, max |diff| in
metres, max relative diff, max distance in units in the last place.
"""
import os, sys
import numpy as np

A, X, M = sys.argv[1:4]
hours = list(range(0, 121)) + list(range(123, 385, 3))


def ulps(a, b):
    ia, ib = a.view(np.int64), b.view(np.int64)   # thickness is positive, so the int order is the float order
    return np.abs(ia - ib)


for name, other in (("intel_vs_graviton4", X), ("amd_vs_graviton4", M), ("amd_vs_intel", None)):
    ncell = ndiff = 0
    mabs = mrel = 0.0
    mulp = 0
    for h in hours:
        f = "f%03d.npy" % h
        a = np.load(os.path.join(A if other else X, f))
        b = np.load(os.path.join(other if other else M, f))
        if a.shape != (721, 1440) or b.shape != a.shape or not (np.isfinite(a).all() and np.isfinite(b).all()):
            sys.exit("FAIL: %s %s bad shape or non-finite" % (name, f))
        d = a != b
        ncell += a.size
        ndiff += int(d.sum())
        if d.any():
            mabs = max(mabs, float(np.abs(a - b).max()))
            mrel = max(mrel, float((np.abs(a - b) / np.abs(a)).max()))
            mulp = max(mulp, int(ulps(a, b).max()))
    print("%s\tcells %d\tdiffer %d (%.4f%%)\tmax_abs_m %.3e\tmax_rel %.3e\tmax_ulp %d"
          % (name, ncell, ndiff, 100.0 * ndiff / ncell, mabs, mrel, mulp))
print("COMPARE OK")
