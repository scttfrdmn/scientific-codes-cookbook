"""Scanpy leg of seurat-real: QC, normalise, HVG, PCA, kNN, leiden + louvain on pbmc_10k_v3.

    python3 -u scanpy_leg.py <outdir>

Writes into <outdir> the contract the Seurat leg consumes (retained cells and genes, the HVG list,
per-cell library sizes) and its own labels. Same parameters as recipes/seurat, plus a mito filter,
which a real 10k v3 run needs and pbmc3k's recipe never applied.
"""
import os, resource, sys, time
import numpy as np, pandas as pd, scanpy as sc

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
T0 = time.time()


def ver(pkg):
    try:
        from importlib.metadata import version
        return str(version(pkg))
    except Exception:
        return "unknown"


print("version\tscanpy %s" % ver("scanpy"))
print("version\tanndata %s" % ver("anndata"))
MTX = "filtered_feature_bc_matrix/"
A = sc.read_10x_mtx(MTX, var_names="gene_ids", cache=False)
if len(set(A.var_names)) != A.n_vars:
    sys.exit("FAIL: gene IDs are not unique; the join key is unsound")
print("raw\t%d cells x %d genes" % (A.n_obs, A.n_vars))
if (A.n_obs, A.n_vars) != (11769, 33538):
    sys.exit("FAIL: raw dims %d x %d, expected 11769 x 33538 (pbmc_10k_v3)" % (A.n_obs, A.n_vars))

A.var["mt"] = A.var["gene_symbols"].str.startswith("MT-")
print("mito_genes\t%d" % int(A.var["mt"].sum()))
sc.pp.calculate_qc_metrics(A, qc_vars=["mt"], percent_top=None, log1p=False, inplace=True)
sc.pp.filter_cells(A, min_genes=200)
A = A[A.obs["pct_counts_mt"] < 20].copy()
sc.pp.filter_genes(A, min_cells=3)
print("after_qc\t%d cells x %d genes" % (A.n_obs, A.n_vars))

# Library size over the RETAINED genes, as an integer: the Seurat leg must compute the identical
# number for every cell from its own read of the matrix (an exact cross-tool identity).
lib = np.asarray(A.X.sum(axis=1, dtype=np.float64)).ravel()
if not np.all(lib == np.round(lib)):
    sys.exit("FAIL: raw counts are not integers")
pd.DataFrame({"barcode": A.obs_names, "lib": lib.astype(np.int64)}).to_csv(
    os.path.join(OUT, "libsize.csv"), index=False)
for name, items in (("keep_cells.txt", A.obs_names), ("keep_genes.txt", A.var_names)):
    with open(os.path.join(OUT, name), "w") as fh:
        fh.write("\n".join(items) + "\n")

sc.pp.normalize_total(A, target_sum=1e4)
# Library-size normalisation identity: every cell sums to target_sum. X is float32 (eps 6e-8), so
# the bound is float32 accumulation, not a band on an observed value.
rs = np.asarray(A.X.sum(axis=1, dtype=np.float64)).ravel()
dev = float(np.max(np.abs(rs - 1e4)) / 1e4)
print("norm_identity\tmax |sum-1e4|/1e4 = %.3e over %d cells" % (dev, A.n_obs))
if not dev < 1e-5:
    sys.exit("FAIL: normalize_total does not sum to 1e4 per cell (%.3e)" % dev)
sc.pp.log1p(A)
full = A.copy()
sc.pp.highly_variable_genes(A, n_top_genes=2000, flavor="seurat")
hvg = list(A.var_names[A.var.highly_variable])
print("hvg\t%d" % len(hvg))
if len(hvg) != 2000:
    sys.exit("FAIL: %d highly-variable genes selected, expected 2000" % len(hvg))
with open(os.path.join(OUT, "hvg.txt"), "w") as fh:
    fh.write("\n".join(hvg) + "\n")

A = A[:, hvg].copy()
sc.pp.scale(A, max_value=10)
sc.tl.pca(A, n_comps=40, svd_solver="arpack", random_state=0)
sc.pp.neighbors(A, n_neighbors=10, n_pcs=40, random_state=0)
sc.tl.leiden(A, key_added="leiden", random_state=0, flavor="igraph", n_iterations=2, directed=False)
sc.tl.louvain(A, key_added="louvain", random_state=0, flavor="igraph")
print("scanpy_leiden_clusters\t%d" % A.obs["leiden"].nunique())
print("scanpy_louvain_clusters\t%d" % A.obs["louvain"].nunique())
pd.DataFrame({"barcode": A.obs_names,
              "leiden": A.obs["leiden"].astype(str).values,
              "louvain": A.obs["louvain"].astype(str).values}).to_csv(
    os.path.join(OUT, "scanpy_labels.csv"), index=False)

sym2id = {}
for gid, sym in zip(full.var_names, full.var["gene_symbols"]):
    sym2id.setdefault(sym, []).append(gid)
mk = {}
for g in ("CD3D", "LYZ", "MS4A1", "NKG7"):
    ids = sym2id.get(g, [])
    if len(ids) != 1:
        sys.exit("FAIL: marker %s maps to %d retained gene IDs; expected exactly 1" % (g, len(ids)))
    mk[g] = np.asarray(full[:, ids[0]].X.todense()).ravel()
pd.DataFrame({"barcode": full.obs_names, **mk}).to_csv(os.path.join(OUT, "markers.csv"), index=False)
print("wall_scanpy_s\t%.1f" % (time.time() - T0))
print("peak_rss_scanpy_kib\t%d" % resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
print("SCANPY OK")
