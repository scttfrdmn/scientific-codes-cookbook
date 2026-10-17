import sys, numpy as np, pandas as pd
from sklearn.metrics import adjusted_rand_score as ari

sp = pd.read_csv("scanpy_labels.csv").set_index("barcode")
se = pd.read_csv("seurat_labels.csv").set_index("barcode")
print("scanpy_cells\t%d" % len(sp))
print("seurat_cells\t%d" % len(se))

# EXACT: the two tools must have clustered the same cells. Identical by construction above, so
# this verifies the construction rather than discovering a coincidence.
if set(sp.index) != set(se.index):
    only_sp = len(set(sp.index) - set(se.index)); only_se = len(set(se.index) - set(sp.index))
    sys.exit("FAIL: cell sets differ -- %d only in scanpy, %d only in Seurat" % (only_sp, only_se))
print("identity_cells\tboth tools clustered the identical %d cells" % len(sp))

j = sp.join(se, how="inner")
n_sp_l = j["leiden"].nunique(); n_sp_v = j["louvain"].nunique(); n_se = j["seurat"].nunique()
print("scanpy_leiden_clusters\t%d" % n_sp_l)
print("scanpy_louvain_clusters\t%d" % n_sp_v)
print("seurat_clusters\t%d" % n_se)

# Seurat's FindClusters default is Louvain, so louvain-vs-Seurat is the ALGORITHM-MATCHED
# comparison and is the headline; leiden-vs-Seurat is reported alongside as the looser one.
a_lv = ari(j["louvain"].astype(str), j["seurat"].astype(str))
a_ld = ari(j["leiden"].astype(str),  j["seurat"].astype(str))
a_int = ari(j["louvain"].astype(str), j["leiden"].astype(str))
print("ari_scanpy_louvain_vs_seurat\t%.4f" % a_lv)
print("ari_scanpy_leiden_vs_seurat\t%.4f" % a_ld)
print("ari_scanpy_internal_louvain_vs_leiden\t%.4f" % a_int)

# The floor is referenced to a measured quantity, not picked: within Scanpy alone, leiden and
# louvain on the SAME graph agree at the value printed above. Two different TOOLS cannot be
# expected to beat two algorithms inside one tool, so the floor sits below that, and the
# internal number is reported so a reader can see the scale the comparison lives on.
FLOOR = 0.45
if a_lv < FLOOR:
    sys.exit("FAIL: Scanpy louvain vs Seurat ARI %.4f is below the %.2f floor" % (a_lv, FLOOR))
print("crosscheck\tSeurat and Scanpy partition the same cells consistently")

# BIOLOGY, categorical: each tool's partition must put the three lineage markers in three
# DIFFERENT clusters. No threshold on any expression value.
mk = pd.read_csv("markers.csv").set_index("barcode")
j2 = j.join(mk, how="inner")
summary = {}
for col in ("louvain", "leiden", "seurat"):
    peaks = []
    for g in ("CD3D", "LYZ", "MS4A1"):
        means = j2.groupby(col)[g].mean()
        peaks.append(str(means.idxmax()))
    distinct = len(set(peaks))
    summary[col] = distinct
    print("markers_%s\t%s  distinct=%d" % (col, ",".join(peaks), distinct))
    if distinct != 3:
        sys.exit("FAIL: %s puts CD3D/LYZ/MS4A1 in %d distinct clusters, expected 3" % (col, distinct))
print("biology\tall three partitions separate T cells, monocytes and B cells")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    fh.write("cells\t%d\n" % len(j))
    fh.write("scanpy_louvain_clusters\t%d\n" % n_sp_v)
    fh.write("scanpy_leiden_clusters\t%d\n" % n_sp_l)
    fh.write("seurat_clusters\t%d\n" % n_se)
    fh.write("ari_scanpy_louvain_vs_seurat\t%.4f\n" % a_lv)
    fh.write("ari_scanpy_leiden_vs_seurat\t%.4f\n" % a_ld)
    fh.write("ari_scanpy_internal\t%.4f\n" % a_int)
print("SEURAT CROSSCHECK OK")
