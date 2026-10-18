"""scanpy-real: the standard Scanpy pipeline on 161,764 PBMCs (Hao et al. 2021), timed per step,
on a cell-count ladder.

    python3 -u pipeline.py <outdir> <n_cells> [<n_cells> ...]

Each rung is the first n cells of one seeded permutation of the whole dataset, so the rungs nest;
the full count (161764) must come last and is the whole dataset. Every step is timed per rung.
On the full rung the checks run and the result is written to <outdir>: labels, HVG list, per-cell
library sizes, PCA/UMAP coordinates and the top marker genes, for the determinism and cross-chip
comparisons (compare.py).
"""
import os, resource, sys, time
import numpy as np, pandas as pd

H5AD = "ed5d841d-6346-47d4-ab2f-7119ad7e3a35.h5ad"
FULL, NGENES, NNZ = 161764, 20525, 353548607
OUT = sys.argv[1]
RUNGS = [int(x) for x in sys.argv[2:]]
if FULL in RUNGS and RUNGS[-1] != FULL:
    sys.exit("the full rung must be last (it reuses the loaded matrix in place)")
os.makedirs(OUT, exist_ok=True)


def ver(pkg):
    # A version print must never be able to fail the run: importlib metadata, str() of the value.
    try:
        from importlib.metadata import version
        return str(version(pkg))
    except Exception:
        return "unknown"


for p in ("scanpy", "anndata", "numpy", "scipy", "scikit-learn", "leidenalg", "igraph",
          "umap-learn", "pynndescent", "numba"):
    print("version\t%s %s" % (p, ver(p)))
import h5py, scanpy as sc, anndata
from anndata.io import read_elem
try:
    from threadpoolctl import threadpool_info
    for i in threadpool_info():
        print("blas\t%s %s %s threads=%s" % (str(i.get("internal_api")), str(i.get("version")),
                                             str(i.get("architecture")), str(i.get("num_threads"))))
except Exception as e:
    print("blas\tunknown (%s)" % str(e))
sc.settings.verbosity = 1
sc.settings.n_jobs = int(os.environ.get("OMP_NUM_THREADS", "1"))


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def rss_gib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20


def rows_chunks(X, step=8192):
    for a in range(0, X.shape[0], step):
        yield a, min(a + step, X.shape[0])


# ------------------------------------------------------------------ load: counts are in raw.X
# CELLxGENE schema: X is the submitter's normalised matrix, raw.X the integer counts. Read only
# the counts (and the obs columns used), exactly what a lab starts from.
t = time.time()
with h5py.File(H5AD, "r") as f:
    X = read_elem(f["raw/X"])
    var = read_elem(f["raw/var"])
    obs = read_elem(f["obs"])[["donor_id", "celltype.l1", "celltype.l2", "nCount_RNA"]]
base = anndata.AnnData(X=X, obs=obs, var=pd.DataFrame({"gene_symbols": var["feature_name"].astype(str).values},
                                                      index=var.index))
del X, var, obs
print("load_s\t%.1f" % (time.time() - t))
print("raw\t%d cells x %d genes, %d non-zero" % (base.n_obs, base.n_vars, base.X.nnz))
if (base.n_obs, base.n_vars, base.X.nnz) != (FULL, NGENES, NNZ):
    fail("raw matrix is %d x %d with %d non-zero, expected %d x %d with %d"
         % (base.n_obs, base.n_vars, base.X.nnz, FULL, NGENES, NNZ))
d = base.X.data
if not (d.dtype == np.float32 and np.all(d == np.round(d)) and d.min() >= 1):
    fail("raw.X is not positive integer counts")
del d
base.var_names_make_unique()
perm = np.random.default_rng(0).permutation(base.n_obs)

# A 2,000-cell warm-up so numba's JIT compilation (pynndescent, umap) lands outside every rung.
t = time.time()
W = base[perm[:2000]].copy()
sc.pp.normalize_total(W, target_sum=1e4); sc.pp.log1p(W)
sc.pp.highly_variable_genes(W, n_top_genes=500, flavor="seurat")
W = W[:, W.var.highly_variable].copy(); sc.pp.scale(W, max_value=10)
sc.tl.pca(W, n_comps=20, svd_solver="arpack", random_state=0)
sc.pp.neighbors(W, n_neighbors=15, n_pcs=20, random_state=0)
sc.tl.leiden(W, random_state=0, flavor="igraph", n_iterations=2, directed=False)
sc.tl.umap(W, random_state=0)
del W
print("warmup_s\t%.1f" % (time.time() - t))

STEPS = ["qc", "normalize", "log1p", "hvg", "scale", "pca", "neighbors", "leiden", "louvain",
         "umap", "rank_genes_groups"]
rows = []
for n in RUNGS:
    full_rung = (n == FULL)
    if full_rung:
        A, base = base, None      # the last rung: no second copy of 2.8 GB of counts
    else:
        A = base[np.sort(perm[:n])].copy()
    T = {}

    def step(name, fn):
        t0 = time.time(); fn(); T[name] = time.time() - t0

    def qc():
        global A
        A.var["mt"] = A.var["gene_symbols"].str.startswith("MT-")
        sc.pp.calculate_qc_metrics(A, qc_vars=["mt"], percent_top=None, log1p=False, inplace=True)
        sc.pp.filter_cells(A, min_genes=200)
        A = A[A.obs["pct_counts_mt"] < 20].copy()
        sc.pp.filter_genes(A, min_cells=3)
    step("qc", qc)

    if full_rung:
        print("after_qc\t%d cells x %d genes" % (A.n_obs, A.n_vars))
        # Per-cell counts, conserved. The library size summed from the retained CSR rows (float64)
        # is integer-valued, and is what normalize_total must divide each cell by.
        lib = np.asarray(A.X.sum(axis=1, dtype=np.float64)).ravel()
        if not np.all(lib == np.round(lib)):
            fail("library sizes are not integers")
        print("libsize\ttotal %d counts over %d cells" % (int(lib.sum()), A.n_obs))
        # Reported: how the submitters' own nCount_RNA (Seurat, before CELLxGENE curation dropped
        # genes) compares -- equal where no dropped gene was counted.
        pub = A.obs["nCount_RNA"].to_numpy(dtype=np.float64)
        print("libsize_vs_published_nCount_RNA\t%d of %d cells equal, max published-ours %d"
              % (int((pub == lib).sum()), A.n_obs, int(np.max(pub - lib))))
        pd.DataFrame({"barcode": A.obs_names, "lib": lib.astype(np.int64)}).to_csv(
            os.path.join(OUT, "libsize.csv"), index=False)
        pre_indptr, pre_indices, pre_data = A.X.indptr.copy(), A.X.indices.copy(), A.X.data.copy()

    step("normalize", lambda: sc.pp.normalize_total(A, target_sum=1e4))

    if full_rung:
        X = A.X
        # Identity 1: every cell sums to target_sum (float32 accumulation bound, not a band).
        rs = np.asarray(X.sum(axis=1, dtype=np.float64)).ravel()
        dev = float(np.max(np.abs(rs - 1e4)) / 1e4)
        print("norm_rowsum\tmax |sum-1e4|/1e4 = %.3e over %d cells" % (dev, A.n_obs))
        if not dev < 1e-5:
            fail("normalize_total does not sum to 1e4 per cell (%.3e)" % dev)
        # Identity 2: normalisation rescales, never moves, a count -- the sparsity pattern is
        # untouched, so every cell's per-gene counts are still where they were.
        same_pattern = np.array_equal(X.indptr, pre_indptr) and np.array_equal(X.indices, pre_indices)
        print("norm_pattern\t%s" % ("identical" if same_pattern else "DIFFERS"))
        if not same_pattern:
            fail("normalize_total changed the sparsity pattern")
        # Identity 3: and it is ONE scale factor per cell -- multiplying back by lib/1e4 recovers
        # every raw count to float32 rounding (one division, one multiply: a few ulp, eps 6e-8).
        rel = 0.0
        for a, b in rows_chunks(X):
            lo, hi = X.indptr[a], X.indptr[b]
            s = np.repeat(lib[a:b] / 1e4, np.diff(X.indptr[a:b + 1]))
            back = X.data[lo:hi].astype(np.float64) * s
            raw = pre_data[lo:hi].astype(np.float64)
            rel = max(rel, float(np.max(np.abs(back - raw) / raw)))
        print("norm_recover\tmax rel error recovering raw counts = %.3e over %d values" % (rel, X.nnz))
        if not rel < 1e-6:
            fail("normalize_total is not one scale factor per cell (%.3e)" % rel)
        del pre_indptr, pre_indices, pre_data

    step("log1p", lambda: sc.pp.log1p(A))
    if full_rung:
        # Identity 4: log1p inverts -- expm1 of the log matrix still sums to 1e4 per cell.
        X = A.X; dev = 0.0
        for a, b in rows_chunks(X):
            e = np.expm1(X.data[X.indptr[a]:X.indptr[b]].astype(np.float64))
            cs = np.add.reduceat(e, X.indptr[a:b] - X.indptr[a]) if b > a else e
            dev = max(dev, float(np.max(np.abs(cs - 1e4)) / 1e4))
        print("log1p_rowsum\tmax |sum(expm1)-1e4|/1e4 = %.3e" % dev)
        if not dev < 1e-5:
            fail("expm1(log1p(X)) does not sum to 1e4 per cell (%.3e)" % dev)

    def hvg():
        global A
        sc.pp.highly_variable_genes(A, n_top_genes=2000, flavor="seurat")
        A.raw = A
        A = A[:, A.var.highly_variable].copy()
    step("hvg", hvg)
    if A.n_vars != 2000:
        fail("%d HVGs selected, expected 2000" % A.n_vars)
    step("scale", lambda: sc.pp.scale(A, max_value=10))
    step("pca", lambda: sc.tl.pca(A, n_comps=50, svd_solver="arpack", random_state=0))
    step("neighbors", lambda: sc.pp.neighbors(A, n_neighbors=15, n_pcs=40, random_state=0))
    step("leiden", lambda: sc.tl.leiden(A, key_added="leiden", random_state=0, flavor="igraph",
                                        n_iterations=2, directed=False))
    step("louvain", lambda: sc.tl.louvain(A, key_added="louvain", random_state=0, flavor="igraph"))
    step("umap", lambda: sc.tl.umap(A, random_state=0))
    step("rank_genes_groups", lambda: sc.tl.rank_genes_groups(A, "leiden", method="wilcoxon",
                                                              use_raw=True, n_genes=25))
    T["total"] = sum(T[s] for s in STEPS)
    row = {"cells": n, "after_qc": A.n_obs, "leiden_clusters": A.obs["leiden"].nunique(),
           "louvain_clusters": A.obs["louvain"].nunique(), "peak_rss_gib": round(rss_gib(), 2),
           **{k: round(v, 2) for k, v in T.items()}}
    rows.append(row)
    print("rung\t" + "\t".join("%s=%s" % (k, str(v)) for k, v in row.items()))

    if full_rung:
        # Published truth: the submitters' level-1 annotation (celltype.l1), which Hao et al. made
        # from RNA *and* 228 surface proteins (WNN), so it is not a restatement of an RNA
        # clustering. Four textbook markers must each peak in a DIFFERENT leiden cluster, and the
        # cluster each peaks in must be majority the lineage the marker names. Categorical: no
        # threshold on any expression value.
        lab = A.obs["leiden"].astype(str).values
        lin = A.obs["celltype.l1"].astype(str).values
        expect = {"CD3D": ("CD4 T", "CD8 T", "other T"), "LYZ": ("Mono",),
                  "MS4A1": ("B",), "NKG7": ("NK",)}
        sym = A.raw.var["gene_symbols"].values
        peaks = []
        for g, want in expect.items():
            idx = np.where(sym == g)[0]
            if len(idx) != 1:
                fail("marker %s maps to %d retained genes" % (g, len(idx)))
            x = np.asarray(A.raw.X[:, idx[0]].todense()).ravel()
            means = pd.Series(x).groupby(lab).mean()
            top = str(means.idxmax())
            maj = pd.Series(lin[lab == top]).value_counts()
            share = float(maj.iloc[0]) / maj.sum()
            peaks.append(top)
            print("marker\t%-5s peaks in cluster %-3s (%.3f vs next %.3f); cluster is %.1f%% %s"
                  % (g, top, means.max(), means.sort_values().iloc[-2], 100 * share, str(maj.index[0])))
            if maj.index[0] not in want:
                fail("%s peaks in a cluster that is majority %s, expected %s" % (g, maj.index[0], want))
        if len(set(peaks)) != 4:
            fail("CD3D/LYZ/MS4A1/NKG7 peak in %d distinct clusters" % len(set(peaks)))
        print("markers_distinct\tyes (%s)" % ",".join(peaks))
        # Reported, not asserted: how cleanly the clusters respect the submitters' lineages.
        from sklearn.metrics import adjusted_rand_score as ari
        ct = pd.crosstab(lab, lin)
        print("lineage_purity_l1\t%.4f" % (float(ct.max(axis=1).sum()) / ct.values.sum()))
        print("ari_leiden_vs_celltype_l1\t%.4f" % ari(lin, lab))
        print("ari_leiden_vs_celltype_l2\t%.4f" % ari(A.obs["celltype.l2"].astype(str).values, lab))
        print("ari_leiden_vs_louvain\t%.4f" % ari(lab, A.obs["louvain"].astype(str).values))

        pd.DataFrame({"barcode": A.obs_names, "leiden": lab,
                      "louvain": A.obs["louvain"].astype(str).values,
                      "celltype_l1": lin}).to_csv(os.path.join(OUT, "labels.csv"), index=False)
        with open(os.path.join(OUT, "hvg.txt"), "w") as fh:
            fh.write("\n".join(A.var_names) + "\n")
        np.save(os.path.join(OUT, "umap.npy"), A.obsm["X_umap"])
        np.save(os.path.join(OUT, "pca.npy"), A.obsm["X_pca"][:, :10])
        pd.DataFrame(A.uns["rank_genes_groups"]["names"]).to_csv(
            os.path.join(OUT, "markers_top25.csv"), index=False)
    del A

pd.DataFrame(rows).to_csv(os.path.join(OUT, "steps.csv"), index=False)
print("peak_rss_gib\t%.2f" % rss_gib())
print("PIPELINE OK")
