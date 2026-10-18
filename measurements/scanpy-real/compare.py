"""scanpy-real: within-chip determinism (two fresh processes, same box) or cross-chip comparison.

    python3 -u compare.py <dirA> <dirB> [--report]

Without --report (in-task, same chip): QC cell set, library sizes, HVGs, leiden labels, PCA, UMAP
and the marker table must be byte-identical between the two runs; louvain is reported. With
--report (two chips' outputs, offline): everything is reported, nothing asserted -- the library
sizes are integer fixed points, the rest is a path through blocked linear algebra.
"""
import filecmp, os, sys
import numpy as np, pandas as pd
from sklearn.metrics import adjusted_rand_score as ari

A, B = sys.argv[1], sys.argv[2]
REPORT = "--report" in sys.argv
bad = []


def same_file(f):
    s = filecmp.cmp(os.path.join(A, f), os.path.join(B, f), shallow=False)
    print("same_%s\t%s" % (f, "identical" if s else "DIFFERS"))
    if not s:
        bad.append(f)
    return s


for f in ("libsize.csv", "hvg.txt", "markers_top25.csv"):
    same_file(f)
if REPORT:
    h1 = open(os.path.join(A, "hvg.txt")).read().split(); h2 = open(os.path.join(B, "hvg.txt")).read().split()
    print("hvg_overlap\t%d of %d" % (len(set(h1) & set(h2)), len(h1)))
for f in ("pca.npy", "umap.npy"):
    x, y = np.load(os.path.join(A, f)), np.load(os.path.join(B, f))
    eq = x.shape == y.shape and np.array_equal(x, y)
    print("same_%s\t%s (max|diff| %.3e)" % (f, "identical" if eq else "DIFFERS",
                                             float(np.max(np.abs(np.abs(x) - np.abs(y)))) if x.shape == y.shape else float("nan")))
    if not eq:
        bad.append(f)

la = pd.read_csv(os.path.join(A, "labels.csv"), dtype=str).set_index("barcode")
lb = pd.read_csv(os.path.join(B, "labels.csv"), dtype=str).set_index("barcode")
if set(la.index) != set(lb.index):
    print("cells\tDIFFER (%d vs %d)" % (len(la), len(lb)))
    sys.exit(1)
lb = lb.loc[la.index]
print("cells\tidentical set of %d" % len(la))
for col in ("leiden", "louvain"):
    eq = la[col].equals(lb[col])
    print("%s\t%s, %d vs %d clusters, ARI %.4f" % (col, "identical" if eq else "DIFFERS",
                                                   la[col].nunique(), lb[col].nunique(), ari(la[col], lb[col])))
    if not eq and col == "leiden":
        bad.append("leiden")

if REPORT:
    print("COMPARE REPORTED")
    sys.exit(0)
# louvain is NOT in the list: scanpy's igraph louvain is not pinned by random_state (recipes/scanpy).
if bad:
    print("FAIL: differ between two processes on one box: %s" % ", ".join(bad))
    sys.exit(1)
print("DETERMINISM OK")
