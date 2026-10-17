"""seurat-real comparison: cross-tool ARI, within-chip determinism, marker separation.

    python3 -u compare.py r1 r2
r1 is the full run (with markers), r2 the replicate of both legs in fresh processes.
"""
import filecmp, sys
import pandas as pd
from sklearn.metrics import adjusted_rand_score as ari

R1, R2 = sys.argv[1], sys.argv[2]


def labels(d):
    sp = pd.read_csv("%s/scanpy_labels.csv" % d, dtype=str).set_index("barcode")
    se = pd.read_csv("%s/seurat_labels.csv" % d, dtype=str).set_index("barcode")
    if set(sp.index) != set(se.index):
        sys.exit("FAIL: %s: cell sets differ between Scanpy and Seurat" % d)
    return sp.join(se, how="inner")


j1, j2 = labels(R1), labels(R2)
print("identity_cells\tboth tools clustered the identical %d cells" % len(j1))

# Determinism within a chip: two fresh processes of each leg, same digest, same box.
for f in ("keep_cells.txt", "keep_genes.txt", "libsize.csv", "hvg.txt"):
    same = filecmp.cmp("%s/%s" % (R1, f), "%s/%s" % (R2, f), shallow=False)
    print("determinism_%s\t%s" % (f, "identical" if same else "DIFFERS"))
    if not same:
        sys.exit("FAIL: %s differs between two runs on one box" % f)
for col in ("leiden", "seurat"):
    same = j1[col].equals(j2.loc[j1.index, col])
    print("determinism_%s\t%s (ARI r1 vs r2 %.4f)" % (col, "identical" if same else "DIFFERS",
                                                     ari(j1[col], j2.loc[j1.index, col])))
    if not same:
        sys.exit("FAIL: %s labels differ between two runs on one box" % col)
lv = ari(j1["louvain"], j2.loc[j1.index, "louvain"])
print("louvain_r1_vs_r2\tARI %.4f, %d vs %d clusters (random_state=0 does not pin it)"
      % (lv, j1["louvain"].nunique(), j2["louvain"].nunique()))

n = {c: j1[c].nunique() for c in ("leiden", "louvain", "seurat")}
a = {"seurat_vs_leiden": ari(j1["seurat"], j1["leiden"]),
     "seurat_vs_louvain": ari(j1["seurat"], j1["louvain"]),
     "leiden_vs_louvain": ari(j1["leiden"], j1["louvain"])}
for k, v in n.items():
    print("clusters_%s\t%d" % (k, v))
for k, v in a.items():
    print("ari_%s\t%.4f" % (k, v))
FLOOR = 0.45   # recipes/seurat's floor, applied to the seeded pairing
if a["seurat_vs_leiden"] < FLOOR:
    sys.exit("FAIL: Seurat vs Scanpy leiden ARI %.4f below %.2f" % (a["seurat_vs_leiden"], FLOOR))

mk = pd.read_csv("%s/markers.csv" % R1).set_index("barcode")
jm = j1.join(mk, how="inner")
for col in ("leiden", "louvain", "seurat"):
    peaks = [str(jm.groupby(col)[g].mean().idxmax()) for g in ("CD3D", "LYZ", "MS4A1", "NKG7")]
    print("markers_%s\t%s distinct=%d" % (col, ",".join(peaks), len(set(peaks))))
    if len(set(peaks)) != 4:
        sys.exit("FAIL: %s puts CD3D/LYZ/MS4A1/NKG7 in %d distinct clusters" % (col, len(set(peaks))))

j1.reset_index().to_csv("labels.csv", index=False)
with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\ncells\t%d\n" % len(j1))
    for k, v in n.items():
        fh.write("clusters_%s\t%d\n" % (k, v))
    for k, v in a.items():
        fh.write("ari_%s\t%.4f\n" % (k, v))
    fh.write("ari_louvain_r1_vs_r2\t%.4f\n" % lv)
print("SEURAT-REAL COMPARE OK")
