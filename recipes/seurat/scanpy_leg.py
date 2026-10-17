import sys, numpy as np, scanpy as sc, anndata
def ver(pkg):
    # scanpy 1.12 deprecates __version__; importlib.metadata is the supported route. Wrapped
    # so a version print can never fail the run.
    try:
        from importlib.metadata import version
        return version(pkg)
    except Exception:
        return "unknown"
print("version\tscanpy %s" % ver("scanpy"))
print("version\tanndata %s" % ver("anndata"))

# KEY ON ENSEMBL IDs, NOT GENE SYMBOLS. pbmc3k's genes.tsv has 91 DUPLICATED symbols and
# zero duplicated IDs, and the two ecosystems disambiguate duplicates differently -- python's
# var_names_make_unique() produces "A, A-1" while R's make.unique() produces "A, A.1". Joining
# the two tools on deduplicated symbols therefore fails on exactly those 91 genes, which is
# what `subscript out of bounds` meant on the first Seurat attempt. IDs are unique by
# construction, so they are the only safe join key.
A = sc.read_10x_mtx("filtered_gene_bc_matrices/hg19/", var_names="gene_ids", cache=False)
if len(set(A.var_names)) != A.n_vars:
    sys.exit("FAIL: gene IDs are not unique; the join key is unsound")
print("raw\t%d cells x %d genes" % (A.n_obs, A.n_vars))
if (A.n_obs, A.n_vars) != (2700, 32738):
    sys.exit("FAIL: raw dims %d x %d, expected 2700 x 32738" % (A.n_obs, A.n_vars))

sc.pp.filter_cells(A, min_genes=200)
sc.pp.filter_genes(A, min_cells=3)
print("after_qc\t%d cells x %d genes" % (A.n_obs, A.n_vars))

# These two lists are the contract with the R side.
with open("keep_cells.txt", "w") as fh:
    fh.write("\n".join(A.obs_names) + "\n")
with open("keep_genes.txt", "w") as fh:
    fh.write("\n".join(A.var_names) + "\n")

# HVG SELECTION, AND WHY NOT seurat_v3. The obvious choice is flavor="seurat_v3", which
# mimics Seurat's vst on raw counts -- but it requires scikit-misc for its loess fit and that
# package is NOT in this env (measured: ModuleNotFoundError on the first run). It does not
# matter for this comparison: the cross-check requires both tools to use the SAME gene set,
# not a Seurat-flavoured one. flavor="seurat" is scanpy's dispersion-based selection on
# log-normalised data, needs nothing extra, and is handed to Seurat verbatim -- so the feature
# set is identical on both sides, which is the only property the comparison depends on.
sc.pp.normalize_total(A, target_sum=1e4); sc.pp.log1p(A)
full = A.copy()                      # log-normalised, all genes -- markers need these
sc.pp.highly_variable_genes(A, n_top_genes=2000, flavor="seurat")
hvg = list(A.var_names[A.var.highly_variable])
print("hvg\t%d" % len(hvg))
if len(hvg) < 500:
    sys.exit("FAIL: only %d highly-variable genes selected" % len(hvg))
with open("hvg.txt", "w") as fh:
    fh.write("\n".join(hvg) + "\n")

A = A[:, hvg].copy()
sc.pp.scale(A, max_value=10)
sc.tl.pca(A, n_comps=40, svd_solver="arpack", random_state=0)
sc.pp.neighbors(A, n_neighbors=10, n_pcs=40, random_state=0)
sc.tl.leiden(A, key_added="leiden", random_state=0, flavor="igraph", n_iterations=2, directed=False)
sc.tl.louvain(A, key_added="louvain", random_state=0, flavor="igraph")
print("scanpy_leiden_clusters\t%d" % A.obs["leiden"].nunique())
print("scanpy_louvain_clusters\t%d" % A.obs["louvain"].nunique())

import pandas as pd
pd.DataFrame({"barcode": A.obs_names,
              "leiden": A.obs["leiden"].astype(str).values,
              "louvain": A.obs["louvain"].astype(str).values}).to_csv("scanpy_labels.csv", index=False)

# marker expression, written once for both partitions to be scored against
# The matrix is keyed by ID now, so marker SYMBOLS must be translated. genes.tsv is
# col1=Ensembl ID, col2=symbol.
sym2id = {}
for line in open("filtered_gene_bc_matrices/hg19/genes.tsv"):
    gid, sym = line.rstrip("\n").split("\t")[:2]
    sym2id.setdefault(sym, []).append(gid)
mk = {}
for g in ("CD3D", "LYZ", "MS4A1"):
    ids = sym2id.get(g, [])
    if len(ids) != 1:
        sys.exit("FAIL: marker %s maps to %d gene IDs; expected exactly 1" % (g, len(ids)))
    gid = ids[0]
    if gid not in full.var_names:
        sys.exit("FAIL: marker %s (%s) absent from the matrix" % (g, gid))
    mk[g] = np.asarray(full[:, gid].X.todense()).ravel()
    print("marker_id\t%s = %s" % (g, gid))
pd.DataFrame({"barcode": full.obs_names, **mk}).to_csv("markers.csv", index=False)
print("SCANPY OK")
