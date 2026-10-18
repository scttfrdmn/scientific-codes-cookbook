"""scanpy-real cross-chip report, standard library only (runs anywhere, no env needed).

    python3 -I crosschip.py <arm64 result dir> <x86_64 result dir>

Reports, never asserts. The library sizes are integer sums -- a fixed point that must be identical;
everything downstream of PCA is a path through blocked linear algebra (OpenBLAS neoversev2 vs
Cooperlake) and graph optimisation, so it is reported.
"""
import ast, csv, filecmp, math, os, struct, sys
from collections import Counter

A, B = sys.argv[1], sys.argv[2]
for f in ("libsize.csv", "hvg.txt", "markers_top25.csv"):
    print("same_%s\t%s" % (f, "identical" if filecmp.cmp(os.path.join(A, f), os.path.join(B, f), shallow=False) else "DIFFERS"))
ha = open(os.path.join(A, "hvg.txt")).read().split(); hb = open(os.path.join(B, "hvg.txt")).read().split()
print("hvg_overlap\t%d of %d" % (len(set(ha) & set(hb)), len(ha)))


def npy(p):
    with open(p, "rb") as fh:
        assert fh.read(6) == b"\x93NUMPY"
        major = fh.read(2)[0]
        hl = struct.unpack("<H" if major == 1 else "<I", fh.read(2 if major == 1 else 4))[0]
        h = ast.literal_eval(fh.read(hl).decode())
        fmt = {"<f4": "f", "<f8": "d"}[h["descr"]]
        n = math.prod(h["shape"])
        return h["shape"], struct.unpack("<%d%s" % (n, fmt), fh.read(n * struct.calcsize(fmt)))


for f in ("pca.npy", "umap.npy"):
    (sa, xa), (sb, xb) = npy(os.path.join(A, f)), npy(os.path.join(B, f))
    # PCA components carry an arbitrary sign per axis, so compare |values|.
    d = max(abs(abs(p) - abs(q)) for p, q in zip(xa, xb))
    print("%s\t%s, max | |a|-|b| | = %.3e" % (f, "identical" if xa == xb else "differs", d))


def labels(d):
    with open(os.path.join(d, "labels.csv")) as fh:
        return {r["barcode"]: r for r in csv.DictReader(fh)}


def ari(x, y):
    c2 = lambda n: n * (n - 1) / 2
    nij = Counter(zip(x, y)); a = Counter(x); b = Counter(y); n = len(x)
    s = sum(c2(v) for v in nij.values()); sa = sum(c2(v) for v in a.values()); sb = sum(c2(v) for v in b.values())
    e = sa * sb / c2(n)
    return (s - e) / ((sa + sb) / 2 - e)


la, lb = labels(A), labels(B)
print("cells\t%s (%d)" % ("identical set" if set(la) == set(lb) else "DIFFER", len(la)))
keys = sorted(la)
for col in ("leiden", "louvain"):
    x = [la[k][col] for k in keys]; y = [lb[k][col] for k in keys]
    print("%s\t%d vs %d clusters, ARI across chips %.4f" % (col, len(set(x)), len(set(y)), ari(x, y)))
