"""Cross-chip comparison of two seurat-real runs' labels.csv (arm64 vs x86_64). Standard library only.

    python3 -I crosschip.py arm64/labels.csv x86_64/labels.csv

Cluster labels are a path (PCA solver, kNN ties and BLAS kernels all feed them), so this REPORTS
the per-partition ARI between chips; it asserts only that the cell sets are identical.
"""
import csv, sys
from collections import Counter
from math import comb


def load(p):
    with open(p, newline="") as fh:
        return {r["barcode"]: r for r in csv.DictReader(fh)}


def ari(a, b):
    n = len(a)
    s_ab = sum(comb(c, 2) for c in Counter(zip(a, b)).values())
    s_a = sum(comb(c, 2) for c in Counter(a).values())
    s_b = sum(comb(c, 2) for c in Counter(b).values())
    exp = s_a * s_b / comb(n, 2)
    return (s_ab - exp) / ((s_a + s_b) / 2 - exp)


A, B = load(sys.argv[1]), load(sys.argv[2])
if set(A) != set(B):
    sys.exit("FAIL: the two chips retained different cells (%d vs %d)" % (len(A), len(B)))
print("cells\t%d on both chips" % len(A))
keys = sorted(A)
for col in ("seurat", "leiden", "louvain"):
    a = [A[k][col] for k in keys]; b = [B[k][col] for k in keys]
    print("%s\tARI arm64 vs x86_64 %.4f  (%d vs %d clusters, %s)"
          % (col, ari(a, b), len(set(a)), len(set(b)), "identical" if a == b else "differs"))
