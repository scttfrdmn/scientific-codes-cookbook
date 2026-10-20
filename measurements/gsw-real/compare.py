"""Cross-chip comparison of the staged sample arrays (four days of 2023, three oceans).

    python3 compare.py <arm64 sample dir> <intel sample dir> <amd sample dir>

For every array: how many elements differ bit-for-bit, the largest absolute difference and the
largest difference in units in the last place. Inputs (p, t, SP) must be identical -- the same
staged bytes, decoded by the same netCDF4 -- so any difference is the conversion's own.
"""
import glob, os, sys
import numpy as np

dirs = dict(zip(("arm64", "intel", "amd"), sys.argv[1:4]))
names = sorted(os.path.basename(f) for f in glob.glob(os.path.join(dirs["arm64"], "*.npz")))
print("files\t%d" % len(names))
VARS = ("p", "t", "SP", "SA", "CT", "pt0", "sigma0", "rho", "rho_x", "spic", "z", "N2")
for a, b in (("arm64", "intel"), ("arm64", "amd"), ("intel", "amd")):
    tot = {v: [0, 0, 0.0, 0] for v in VARS}    # elements, differing, max|diff|, max ulp
    for n in names:
        A = np.load(os.path.join(dirs[a], n)); B = np.load(os.path.join(dirs[b], n))
        for v in VARS:
            x, y = A[v].ravel(), B[v].ravel()
            if x.shape != y.shape:
                sys.exit("FAIL: %s %s shape differs %s vs %s" % (n, v, x.shape, y.shape))
            both = np.isfinite(x) & np.isfinite(y)
            if not np.array_equal(np.isfinite(x), np.isfinite(y)):
                sys.exit("FAIL: %s %s finite pattern differs" % (n, v))
            x, y = x[both], y[both]
            ne = x != y
            t = tot[v]
            t[0] += x.size; t[1] += int(ne.sum())
            if ne.any():
                t[2] = max(t[2], float(np.max(np.abs(x[ne] - y[ne]))))
                ulp = np.abs(x[ne].view(np.int64) - y[ne].view(np.int64))
                t[3] = max(t[3], int(ulp.max()))
    for v in VARS:
        e, d, m, u = tot[v]
        print("%s_vs_%s\t%-7s\telements %d\tdiffer %d\tmax|diff| %.3e\tmax_ulp %d" % (a, b, v, e, d, m, u))
