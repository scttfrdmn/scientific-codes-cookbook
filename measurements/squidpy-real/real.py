"""squidpy-real: squidpy's graph, enrichment, co-occurrence and autocorrelation on two whole sections.

    python3 -u real.py <slideseq|visium> <h5ad> <outdir> <n_jobs>

Every assertion is made here, inside the task, and a failure exits non-zero. Each reference is
computed independently of squidpy: graphs by brute force (or from the Visium array indices), counts
from the one-hot label matrix, co-occurrence from a KD-tree pair list, Moran's I and Geary's C from
their closed forms, the analytic variances from Cliff & Ord with float64 weight moments.

Outputs (in <outdir>): score.tsv (one row per check), phases.tsv (seconds per step), and the
full-precision results the cross-chip and replicate comparisons read: graph edge lists, enrichment
counts and z-scores, co-occurrence, Moran / Geary tables. real.log ends with SQUIDPY REAL OK.
"""
import hashlib
import os
import resource
import sys
import time
import warnings

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.sparse.csgraph import shortest_path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=SyntaxWarning)  # docrep, at squidpy import

NAME, H5AD, OUT, NJ = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
os.makedirs(OUT, exist_ok=True)
SEED = 0
N_PERMS_MORAN = 100     # on the 2,000 HVGs, as squidpy's own tutorials run it; n_jobs workers
N_HVG = 2000
N_PERMS_NHOOD = 1000
LOG = open(os.path.join(OUT, "real.log"), "w")
ROWS, PHASES, FAILED = [], [], []
T0 = time.time()


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


def rec(name, value, bound="", ok=None):
    """One check row. `ok` must be a real bool (numpy bools included); None means reported only."""
    v = value if isinstance(value, str) else str(value)
    status = "report" if ok is None else ("ok" if bool(ok) else "FAIL")
    if status == "FAIL":
        FAILED.append(name)
    ROWS.append((name, v, str(bound), status))
    log(f"{status:6s} {name}: {v}  [{bound}]")


def finite_le(x, bound):
    """x <= bound, and False for NaN (nan > tol is False, so a NaN would pass a one-sided test)."""
    x = np.asarray(x, dtype=np.float64)
    return bool(np.all(np.isfinite(x)) and np.all(x <= bound))


class Phase:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()

    def __exit__(self, *a):
        dt = time.time() - self.t
        PHASES.append((self.name, f"{dt:.2f}"))
        log(f"phase {self.name} {dt:.2f} s  (maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20:.2f} GiB)")


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


def sha(arr):
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()


def save_tsv(df, fn):
    df.to_csv(os.path.join(OUT, fn), sep="\t", float_format="%.17g")


import anndata as ad  # noqa: E402
import scanpy as sc  # noqa: E402
import squidpy as sq  # noqa: E402
import numba  # noqa: E402

NUMBA_T = numba.get_num_threads()
THREADS_SEEN = []


def restore_threads(after):
    """squidpy's permutation runner calls numba.set_num_threads(1) in the CALLING thread when it runs
    in-process (n_jobs=1, or the threading backend), so every numba-parallel kernel after it in the
    session runs on one thread. Record what it left, then put the box's count back."""
    THREADS_SEEN.append((after, numba.get_num_threads()))
    numba.set_num_threads(NUMBA_T)

for m in (sq, sc, ad, np, pd, __import__("scipy"), __import__("sklearn"), __import__("numba")):
    log("version", m.__name__, ver(m))
log("n_jobs", NJ, "seed", SEED)

# ------------------------------------------------------------------ load
with Phase("load"):
    a = ad.read_h5ad(H5AD)
counts = a.raw.X if a.raw is not None else a.X
counts = sp.csr_matrix(counts)
d = counts.data
rec("counts are non-negative integers", f"{a.n_obs} x {a.n_vars}, nnz {counts.nnz}, max {d.max():.0f}",
    "all data == round(data) >= 0", bool(np.all(d >= 0) and np.all(d == np.round(d))))
rec("obs names unique", int(a.obs_names.is_unique), "1", bool(a.obs_names.is_unique))
if NAME == "visium":
    keep = np.asarray(a.obs["in_tissue"]).astype(int) == 1
    rec("Visium spots in tissue", f"{int(keep.sum())} of {a.n_obs}", "reported")
    a = a[keep].copy()
    counts = counts[keep]
xy = np.asarray(a.obsm["spatial"], dtype=np.float64)
rec("coordinates finite", f"{xy.shape}", "all finite", bool(np.all(np.isfinite(xy))))
n = a.n_obs
labels = a.obs["cell_type"].astype("category")
a.obs["cell_type"] = labels.cat.remove_unused_categories()
cats = list(a.obs["cell_type"].cat.categories)
rec("cell types (authors' annotation)", f"{len(cats)}: " + "; ".join(f"{c}={int((a.obs['cell_type'] == c).sum())}" for c in cats), "reported")

# expression: log1p(normalize_total), every gene detected in >= 10 observations
b = ad.AnnData(X=counts.astype(np.float64), obs=a.obs.copy(), var=a.var.copy())
b.obsm["spatial"] = xy
sc.pp.normalize_total(b, target_sum=1e4)
sc.pp.log1p(b)
ncell = np.asarray((counts > 0).sum(0)).ravel()
gmask = ncell >= 10
b = b[:, gmask].copy()
b.X = sp.csr_matrix(b.X)
rec("genes tested (detected in >= 10 observations)", f"{b.n_vars} of {a.n_vars}", "reported")


# ------------------------------------------------------------------ helpers
def brute_knn(P, k, chunk=512):
    """The k nearest other points of every point, by exhaustive float64 distance, with the (k+1)-th."""
    nn = np.empty((len(P), k), dtype=np.int64)
    dk = np.empty((len(P), k + 1))
    sq_ = (P * P).sum(1)
    for s in range(0, len(P), chunk):
        e = min(s + chunk, len(P))
        D = np.sqrt(np.maximum(((P[s:e, None, :] - P[None, :, :]) ** 2).sum(-1), 0.0))
        D[np.arange(e - s), np.arange(s, e)] = np.inf
        part = np.argpartition(D, k + 1, axis=1)[:, : k + 1]
        pd_ = np.take_along_axis(D, part, 1)
        o = np.argsort(pd_, axis=1, kind="stable")
        part, pd_ = np.take_along_axis(part, o, 1), np.take_along_axis(pd_, o, 1)
        nn[s:e], dk[s:e] = part[:, :k], pd_
    del sq_
    return nn, dk


def brute_radius(P, r, chunk=512):
    rows, cols, dist, near = [], [], [], 0
    for s in range(0, len(P), chunk):
        e = min(s + chunk, len(P))
        D = np.sqrt(((P[s:e, None, :] - P[None, :, :]) ** 2).sum(-1))
        D[np.arange(e - s), np.arange(s, e)] = np.inf
        near += int(np.sum(np.abs(D - r) <= 1e-9 * r))
        i, j = np.nonzero(D <= r)
        rows.append(i + s); cols.append(j); dist.append(D[i, j])
    return np.concatenate(rows), np.concatenate(cols), np.concatenate(dist), near


def closed_form(W, X, mode):
    """Moran's I / Geary's C of every column of sparse X under weights W, in float64, chunked. Geary's
    numerator is summed edge by edge, sum_ij w_ij (y_i - y_j)^2, so it has no cancellation."""
    W = sp.csr_matrix(W, dtype=np.float64)
    S0 = W.sum()
    Wc = W.tocoo()
    out = np.empty(X.shape[1])
    step = 1000 if mode == "moran" else 200
    for s in range(0, X.shape[1], step):
        Y = X[:, s:s + step].toarray()
        Z = Y - Y.mean(0)
        if mode == "moran":
            out[s:s + step] = (len(Y) / S0) * (Z * (W @ Z)).sum(0) / (Z * Z).sum(0)
        else:
            D = Y[Wc.row] - Y[Wc.col]
            out[s:s + step] = (len(Y) - 1) * (Wc.data[:, None] * D * D).sum(0) / (2 * S0 * (Z * Z).sum(0))
    return out


def affine_bound(W, X, I, a=3.7, c=112.5):
    """Per-gene bound on |I(a*y + c) - I(y)| in float64. Forming a*y + c rounds each element by at most
    eta = 2u(c + a*max|y|) (u = 2^-53), i.e. eta/a in units of y; to first order that moves I by at most
    (n/S0) * eta/a * (||(W + W^T) z||_1 + 2|I| ||z||_1) / z'z. Each of the two evaluations of I also carries
    the recursive-summation bound n*u on its sums: 2nu((n/S0) * sum|z * Wz| / z'z + |I|)."""
    u = 2.0 ** -53
    W = sp.csr_matrix(W, dtype=np.float64)
    n, S0 = W.shape[0], W.sum()
    WW = W + W.T
    out = np.empty(X.shape[1])
    for s in range(0, X.shape[1], 1000):
        Y = X[:, s:s + 1000].toarray()
        Z = Y - Y.mean(0)
        zz = (Z * Z).sum(0)
        Ii = np.abs(I[s:s + 1000])
        eta = 2 * u * (c + a * np.abs(Y).max(0)) / a
        b1 = (n / S0) * eta * (np.abs(WW @ Z).sum(0) + 2 * Ii * np.abs(Z).sum(0)) / zz
        b2 = 2 * n * u * ((n / S0) * np.abs(Z * (W @ Z)).sum(0) / zz + Ii)
        out[s:s + 1000] = b1 + b2
    return out


def sum_bounds(W, X):
    """Per-gene float64 rounding envelopes for two independent evaluations of each statistic, from the
    recursive-summation bound n*u (u = 2^-53) on every sum they take:
      Moran: 2nu((n/S0) sum|z * Wz| / z'z + |I|), the numerator's terms being signed;
      Geary: 4nu C (1 + sum y^2 / z'z), the second term because scanpy expands sum z^2 as
             sum y^2 - 2 ybar sum y + n ybar^2, which cancels when the mean is large against the spread."""
    u = 2.0 ** -53
    W = sp.csr_matrix(W, dtype=np.float64)
    n, S0 = W.shape[0], W.sum()
    absnum, ratio2 = np.empty(X.shape[1]), np.empty(X.shape[1])
    for s in range(0, X.shape[1], 1000):
        Y = X[:, s:s + 1000].toarray()
        Z = Y - Y.mean(0)
        zz = (Z * Z).sum(0)
        absnum[s:s + 1000] = (n / S0) * np.abs(Z * (W @ Z)).sum(0) / zz
        ratio2[s:s + 1000] = (Y * Y).sum(0) / zz
    return lambda I: 2 * n * u * (absnum + np.abs(I)), lambda C: 4 * n * u * np.abs(C) * (1 + ratio2)


def cliff_ord(W, mode):
    W = sp.csr_matrix(W, dtype=np.float64)
    nn_ = W.shape[0]
    S0 = W.sum()
    S1 = 0.5 * ((W + W.T).multiply(W + W.T)).sum()
    S2 = float(((np.asarray(W.sum(1)).ravel() + np.asarray(W.sum(0)).ravel()) ** 2).sum())
    if mode == "moran":
        return (nn_ * nn_ * S1 - nn_ * S2 + 3 * S0 * S0) / ((nn_ - 1) * (nn_ + 1) * S0 * S0) - (1.0 / (nn_ - 1)) ** 2
    return ((2 * S1 + S2) * (nn_ - 1) - 4 * S0 * S0) / (2 * (nn_ + 1) * S0 * S0)


def edges(A):
    A = sp.coo_matrix(A)
    o = np.lexsort((A.col, A.row))
    return np.stack([A.row[o], A.col[o]], 1).astype(np.int64)


def onehot(lab):
    codes = pd.Categorical(lab).codes
    return sp.csr_matrix((np.ones(len(codes)), (np.arange(len(codes)), codes)), shape=(len(codes), codes.max() + 1))


def enrichment(adata, key, conn, tag, n_jobs=1, backend="loky"):
    with Phase(f"nhood_enrichment[{tag}]"):
        r = sq.gr.nhood_enrichment(adata, cluster_key=key, connectivity_key=conn, n_perms=N_PERMS_NHOOD,
                                   seed=SEED, n_jobs=n_jobs, backend=backend, copy=True, show_progress_bar=False)
    restore_threads(f"nhood_enrichment[{tag}]")
    z, cnt = np.asarray(r[0], dtype=np.float64), np.asarray(r[1])
    M = onehot(adata.obs[key])
    A = sp.csr_matrix(adata.obsp[f"{conn}_connectivities"])
    A.data[:] = 1.0
    ref = (M.T @ A @ M).toarray()
    rec(f"{tag}: enrichment counts == M'AM, sum == edges", f"max |diff| {np.abs(cnt - ref).max():.0f}; sum {cnt.sum():.0f} vs {A.nnz}",
        "== 0 exactly", bool(np.array_equal(cnt, ref) and cnt.sum() == A.nnz))
    return z, cnt


# ------------------------------------------------------------------ graphs
if NAME == "slideseq":
    K = 6
    with Phase("spatial_neighbors_knn"):
        sq.gr.spatial_neighbors_knn(a, n_neighs=K, key_added="knn")
    with Phase("brute_force_knn"):
        nn, dk = brute_knn(xy, K)
    A = sp.csr_matrix(a.obsp["knn_distances"])
    A.sort_indices()
    rows_ok = np.diff(A.indptr)
    rec("kNN graph: every bead has exactly k neighbours", f"min {rows_ok.min()} max {rows_ok.max()}", f"== {K}", bool(rows_ok.min() == K == rows_ok.max()))
    sqd = np.sort(A.data.reshape(n, K), 1)
    rel = np.abs(sqd - dk[:, :K]) / np.maximum(dk[:, :K], 1e-300)
    rec("kNN distances == brute force, every bead", f"max rel {rel.max():.3e}", "< 1e-12", finite_le(rel.max(), 1e-12))
    tie = np.abs(dk[:, K] - dk[:, K - 1]) <= 1e-9 * dk[:, K - 1]
    sqset = np.sort(A.indices.reshape(n, K), 1)
    bfset = np.sort(nn, 1)
    diff = ~np.all(sqset == bfset, 1)
    rec("kNN neighbour sets == brute force where the k-th neighbour is unique", f"{int((diff & ~tie).sum())} differ of {n}; {int(tie.sum())} beads tie at k",
        "0 differ", bool((diff & ~tie).sum() == 0))
    np.save(os.path.join(OUT, "knn_edges.npy"), edges(a.obsp["knn_connectivities"]))

    R = 30.0
    with Phase("spatial_neighbors_radius"):
        sq.gr.spatial_neighbors_radius(a, radius=R, key_added="rad")
    with Phase("brute_force_radius"):
        ri, ci, rd, near = brute_radius(xy, R)
    Ar = sp.csr_matrix(a.obsp["rad_distances"])
    Ar = sp.csr_matrix(a.obsp["rad_connectivities"])
    sqe = set(zip(*Ar.nonzero()))
    bfe = set(zip(ri.tolist(), ci.tolist()))
    asym = (Ar != Ar.T).nnz
    rec(f"radius graph (r={R:g}) == brute force", f"{len(sqe ^ bfe)} differing of {len(bfe)}; {near} pairs within 1e-9 r of r; asymmetric {asym}",
        "0 differing, symmetric", bool(len(sqe ^ bfe) == 0 and asym == 0))
    np.save(os.path.join(OUT, "rad_edges.npy"), edges(a.obsp["rad_connectivities"]))
    CONN = "knn"
else:
    ar = np.asarray(a.obs["array_row"], dtype=np.int64)
    ac = np.asarray(a.obs["array_col"], dtype=np.int64)
    rec("Visium array indices on the hex lattice", "array_row + array_col even on every spot", "all even",
        bool(np.all((ar + ac) % 2 == 0)))
    idx = {(r_, c_): i for i, (r_, c_) in enumerate(zip(ar, ac))}
    rec("one spot per array position", f"{len(idx)} positions, {n} spots", "equal", bool(len(idx) == n))
    hr, hc = [], []
    for i, (r_, c_) in enumerate(zip(ar, ac)):
        for dr, dc in ((0, 2), (0, -2), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            j = idx.get((r_ + dr, c_ + dc))
            if j is not None:
                hr.append(i); hc.append(j)
    H = sp.csr_matrix((np.ones(len(hr)), (hr, hc)), shape=(n, n))
    hop = shortest_path(H, method="D", unweighted=True)
    deg1 = np.asarray(H.sum(1)).ravel()
    for k in (1, 2, 3):
        with Phase(f"spatial_neighbors_grid[rings={k}]"):
            sq.gr.spatial_neighbors_grid(a, n_neighs=6, n_rings=k, key_added=f"grid{k}")
        G = sp.csr_matrix(a.obsp[f"grid{k}_connectivities"])
        D = sp.csr_matrix(a.obsp[f"grid{k}_distances"])
        ref = sp.csr_matrix(((hop <= k) & (hop > 0)).astype(np.float64))
        ndiff = (G != ref).nnz
        same = ndiff == 0
        # ring distance stored in the distance matrix must be the hop count
        Dc = D.tocoo()
        ringok = bool(np.all(Dc.data == hop[Dc.row, Dc.col])) if k > 1 else True
        interior = np.asarray((ref.sum(1))).ravel() == 3 * k * (k + 1)
        rec(f"grid graph, {k} ring(s): pixel-space graph == array-index hex lattice, edge for edge",
            f"{ndiff} differing of {ref.nnz}; ring labels {'== hops' if ringok else 'WRONG'}; "
            f"{int(interior.sum())} spots with full {3 * k * (k + 1)}-neighbour rings", "0 differing", bool(same and ringok))
        gdeg = np.asarray(G.sum(1)).ravel()
        rec(f"grid {k} ring(s): degree of every spot whose ring is complete", f"{sorted(set(gdeg[interior].astype(int).tolist()))}", f"== {3 * k * (k + 1)}",
            bool(interior.sum() > 0 and np.all(gdeg[interior] == 3 * k * (k + 1)) and G.diagonal().sum() == 0))
        np.save(os.path.join(OUT, f"grid{k}_edges.npy"), edges(G))
    a.obsp["knn_connectivities"] = a.obsp["grid1_connectivities"]
    a.obsp["knn_distances"] = a.obsp["grid1_distances"]
    CONN = "grid1"

# ------------------------------------------------------------------ neighbourhood enrichment
z_real, cnt_real = enrichment(a, "cell_type", CONN, "authors' cell types")
pd.DataFrame(z_real, index=cats, columns=cats).to_csv(os.path.join(OUT, "nhood_z.tsv"), sep="\t", float_format="%.17g")
pd.DataFrame(cnt_real, index=cats, columns=cats).to_csv(os.path.join(OUT, "nhood_count.tsv"), sep="\t")
diag = np.diag(z_real)
rec("authors' cell types: self-enrichment z per type", "; ".join(f"{c}={v:.1f}" for c, v in zip(cats, diag)), "reported")

# planted: the section cut in half at the median x, on the real positions and the real graph
half = np.where(xy[:, 0] < np.median(xy[:, 0]), "left", "right")
a.obs["half"] = pd.Categorical(half)
zh, _ = enrichment(a, "half", CONN, "planted halves")
rec("planted halves: left-left / right-right z, left-right z", f"{zh[0, 0]:.1f} / {zh[1, 1]:.1f}, {zh[0, 1]:.1f}", "> +5, > +5, < -5",
    bool(zh[0, 0] > 5 and zh[1, 1] > 5 and zh[0, 1] < -5))

# control: the authors' labels shuffled once (fixed seed); the null is then the permutation itself
a.obs["shuffled"] = pd.Categorical(np.random.default_rng(20261020).permutation(np.asarray(a.obs["cell_type"])),
                                   categories=cats)
zs, cs_ = enrichment(a, "shuffled", CONN, "shuffled labels")
M = onehot(a.obs["shuffled"]).toarray().sum(0)
expct = np.outer(M, M) / n / n * a.obsp[f"{CONN}_connectivities"].nnz
big = expct >= 50
rec("shuffled labels: max |z| over pairs with expected count >= 50", f"{np.nanmax(np.abs(zs[big])):.2f} over {int(big.sum())} pairs "
    f"(rare pairs, reported: {np.nanmax(np.abs(np.where(big, 0, zs))):.2f})", "< 5", finite_le(np.nanmax(np.abs(zs[big])), 5.0))
# n_jobs > 1: squidpy's default (loky) backend memory-maps any argument over 1 MB read-only, and the
# counting kernel is compiled for writable arrays only, so a graph over 262,144 edges raises.
nnz_ = a.obsp[f"{CONN}_connectivities"].nnz
try:
    enrichment(a, "cell_type", CONN, f"n_jobs={NJ}, loky", n_jobs=NJ)
    rec(f"nhood_enrichment n_jobs={NJ} (loky), {nnz_} edges", "runs", "reported")
except Exception as e:  # noqa: BLE001 -- reported, not asserted
    restore_threads("nhood_enrichment[loky, raised]")
    rec(f"nhood_enrichment n_jobs={NJ} (loky), {nnz_} edges", f"raises {type(e).__name__}: {str(e).splitlines()[0][:110]}", "reported")
# the seed alone does not pin the null: squidpy seeds each worker's chunk by its first index
zt, _ = enrichment(a, "cell_type", CONN, f"n_jobs={NJ}, threading", n_jobs=NJ, backend="threading")
rec(f"same seed, n_jobs=1 vs n_jobs={NJ} (threading): z-scores", f"max |dz| {np.nanmax(np.abs(zt - z_real)):.3f}; identical: {bool(np.array_equal(zt, z_real))}", "reported")

# ------------------------------------------------------------------ co-occurrence (Slide-seq: continuous positions)
if NAME == "slideseq":
    from scipy.spatial import cKDTree
    RMAX = 200.0
    # squidpy counts pairs in float32. On positions rounded to 1/8 unit, every dx, dx^2 and dx^2 + dy^2 of
    # a pair closer than 256 units is exact in float32 (and 64*d^2 an integer), and each threshold^2 is
    # kept off that integer lattice, so the count at every radius has exactly one right answer.
    # Rounding moves a bead by at most 1/16 unit.
    a.obsm["spatial_q"] = np.round(xy * 8) / 8
    xq = a.obsm["spatial_q"]
    with Phase("co_occurrence_reference_pairs"):
        pr = cKDTree(xq).query_pairs(RMAX * 1.05, output_type="ndarray")
        d2_64 = 64 * ((xq[pr[:, 0]] - xq[pr[:, 1]]) ** 2).sum(1)  # exact integers in float64
    rec("reference pair distances on the 1/8 lattice", f"{len(pr)} pairs within {RMAX * 1.05:g}; 64 d^2 integral: {bool(np.all(d2_64 == np.round(d2_64)))}",
        "integral", bool(np.all(d2_64 == np.round(d2_64))))
    th = []
    for t in np.linspace(10.0, RMAX, 39) + 0.37:
        t = np.float32(t)
        while float(np.float32(t) * np.float32(t)) * 64 == round(float(np.float32(t) * np.float32(t)) * 64):
            t = np.float32(t + np.float32(0.001))
        th.append(t)
    t2_64 = np.array([float(np.float32(t) * np.float32(t)) * 64 for t in th])
    diam = float(np.sqrt(((xq.max(0) - xq.min(0)) ** 2).sum())) * 1.5
    interval = np.array([0.0] + [float(t) for t in th] + [diam], dtype=np.float32)
    with Phase("co_occurrence"):
        occ, _iv = sq.gr.co_occurrence(a, cluster_key="cell_type", spatial_key="spatial_q", interval=interval, copy=True)
    occ = np.asarray(occ, dtype=np.float64)
    lab = np.asarray(a.obs["cell_type"].cat.codes)
    k_ = len(cats)
    cnt = np.zeros((k_, k_, len(th)))
    li, lj = lab[pr[:, 0]], lab[pr[:, 1]]
    for r_i in range(len(th)):
        m = d2_64 <= t2_64[r_i]
        c2 = np.zeros((k_, k_))
        np.add.at(c2, (li[m], lj[m]), 1)
        cnt[:, :, r_i] = c2 + c2.T
    nlab = np.bincount(lab, minlength=k_).astype(np.float64)
    full = np.outer(nlab, nlab) - np.diag(nlab)
    cnt = np.concatenate([cnt, full[:, :, None]], 2)
    rs = cnt.sum(0)
    tot = rs.sum(0)
    ref = np.zeros_like(cnt)
    for r_i in range(cnt.shape[2]):
        pr_ = rs[:, r_i] / tot[r_i]
        for c in range(k_):
            for i in range(k_):
                if pr_[i] != 0 and rs[c, r_i] != 0:
                    ref[i, c, r_i] = (cnt[c, i, r_i] / rs[c, r_i]) / pr_[i]
    err = np.abs(occ - ref)
    rec(f"co-occurrence, {len(th)} radii 10-{RMAX:g} units: == KD-tree pair counts", f"max |diff| {err[:, :, :-1].max():.3e}",
        "< 1e-9", finite_le(err[:, :, :-1].max(), 1e-9))
    exp_last = np.where(np.eye(k_, dtype=bool), n * (nlab[:, None] - 1) / ((n - 1) * nlab[:, None]), n / (n - 1)).T
    rec("co-occurrence past the section's diameter == n/(n-1) off-diagonal, n(n_i-1)/((n-1)n_i) on it",
        f"max |diff| {np.abs(occ[:, :, -1] - exp_last).max():.3e}", "< 1e-9", finite_le(np.abs(occ[:, :, -1] - exp_last).max(), 1e-9))
    np.save(os.path.join(OUT, "co_occurrence.npy"), occ)
    log("cooc thresholds", " ".join(f"{t:.4f}" for t in interval))

# ------------------------------------------------------------------ Moran's I and Geary's C, every gene
b.obsp["w_connectivities"] = a.obsp[f"{CONN}_connectivities"]
# the closed forms are evaluated on the weights squidpy applies: the graph row-normalised in its own dtype
# (float32 for a grid graph, so 1/6 is float32's 1/6), then widened to float64
from sklearn.preprocessing import normalize  # noqa: E402
W = normalize(sp.csr_matrix(b.obsp["w_connectivities"], copy=True), norm="l1", axis=1)
_rs = np.asarray(W.sum(1)).ravel()
rec("weights", f"{W.dtype}, {W.nnz} entries, {int((_rs == 0).sum())} isolated observations, max |row sum - 1| elsewhere "
    f"{np.abs(_rs[_rs > 0] - 1).max():.1e}", "reported")
W = sp.csr_matrix(W, dtype=np.float64)
genes = list(b.var_names)
rec("numba threads squidpy left after each permutation call (restored to %d after each)" % NUMBA_T,
    "; ".join(f"{k}: {v}" for k, v in THREADS_SEEN), "reported")
# what that costs: the same Moran's I call on the first 2,000 tested genes, at the thread count squidpy
# leaves behind and at the box's
g2k = genes[:2000]
for nt in (1, NUMBA_T):
    numba.set_num_threads(nt)
    sq.gr.spatial_autocorr(b, connectivity_key="w_connectivities", genes=g2k[:10], mode="moran", copy=True, show_progress_bar=False)  # compile
    with Phase(f"spatial_autocorr[moran, {len(g2k)} genes, numba threads {nt}]"):
        sq.gr.spatial_autocorr(b, connectivity_key="w_connectivities", genes=g2k, mode="moran", copy=True, show_progress_bar=False)
numba.set_num_threads(NUMBA_T)
with Phase(f"spatial_autocorr[moran, {len(genes)} genes]"):
    mor = sq.gr.spatial_autocorr(b, connectivity_key="w_connectivities", genes=genes, mode="moran", n_perms=None,
                                 copy=True, show_progress_bar=False).loc[genes]
with Phase(f"spatial_autocorr[geary, {len(genes)} genes]"):
    gea = sq.gr.spatial_autocorr(b, connectivity_key="w_connectivities", genes=genes, mode="geary", n_perms=None,
                                 copy=True, show_progress_bar=False).loc[genes]
with Phase("closed forms"):
    Iref = closed_form(W, b.X, "moran")
    Cref = closed_form(W, b.X, "geary")
eI = np.abs(mor["I"].to_numpy() - Iref)
eC = np.abs(gea["C"].to_numpy() - Cref)
with Phase("rounding envelopes"):
    envI, envC = sum_bounds(W, b.X)
    bI, bC = envI(Iref), envC(Cref)
rec(f"Moran's I == closed form, all {len(genes)} genes", f"max |diff| {eI.max():.3e}; max |diff| / per-gene summation bound {(eI / bI).max():.4f} "
    f"(bounds {bI.min():.1e} to {bI.max():.1e})", "<= 1 on every gene", finite_le((eI / bI).max(), 1.0))
rec(f"Geary's C == closed form, all {len(genes)} genes", f"max |diff| {eC.max():.3e}; max |diff| / per-gene summation bound {(eC / bC).max():.4f} "
    f"(bounds {bC.min():.1e} to {bC.max():.1e})", "<= 1 on every gene", finite_le((eC / bC).max(), 1.0))
vI, vC = cliff_ord(W, "moran"), cliff_ord(W, "geary")
rvI = abs(mor["var_norm"].iloc[0] - vI) / vI
rvC = abs(gea["var_norm"].iloc[0] - vC) / vC
rec("analytic var, Moran / Geary vs Cliff & Ord", f"rel {rvI:.1e} / {rvC:.1e}; Geary/Moran {vC / vI:.3f}", "rel < 1e-5 each; Geary != Moran",
    bool(finite_le(rvI, 1e-5) and finite_le(rvC, 1e-5) and abs(vC / vI - 1) > 1e-3))
with Phase("highly_variable_genes"):
    sc.pp.highly_variable_genes(b, n_top_genes=N_HVG)
hvg = [g for g in genes if b.var.loc[g, "highly_variable"]]
with Phase(f"spatial_autocorr[moran, {len(hvg)} HVGs, {N_PERMS_MORAN} perms]"):
    mp = sq.gr.spatial_autocorr(b, connectivity_key="w_connectivities", genes=hvg, mode="moran", n_perms=N_PERMS_MORAN,
                                seed=SEED, n_jobs=NJ, copy=True, show_progress_bar=False).loc[hvg]
restore_threads("spatial_autocorr[perms]")
rec(f"numba threads after spatial_autocorr(n_perms={N_PERMS_MORAN}, n_jobs={NJ})", str(THREADS_SEEN[-1][1]), "reported")
dsub = np.abs(mp["I"].to_numpy() - mor.loc[hvg, "I"].to_numpy())
rec("a gene's I does not depend on which other genes are tested", f"max |diff| {dsub.max():.3e} over {len(hvg)} HVGs", "< 1e-12", finite_le(dsub.max(), 1e-12))
ps = mp["pval_sim"].to_numpy()
rec("permutation p-values in [1/(n_perms+1), 1]", f"min {ps.min():.5f} max {ps.max():.5f}", f">= 1/{N_PERMS_MORAN + 1}",
    bool(np.all(ps >= 1 / (N_PERMS_MORAN + 1) - 1e-15) and np.all(ps <= 1)))
nsig = int((mp["pval_sim_fdr_bh"] < 0.05).sum())
nsn = int((mor["pval_norm_fdr_bh"] < 0.05).sum())
top = mor.sort_values("I", ascending=False).head(10)
fn = b.var["feature_name"].astype(str) if "feature_name" in b.var else pd.Series(b.var_names, index=b.var_names)
rec("spatially variable genes, BH-FDR < 0.05", f"{nsn} of {len(genes)} (pval_norm); {nsig} of {len(hvg)} HVGs (pval_sim); top I: "
    + ", ".join(f"{fn[g]}={v:.3f}" for g, v in top["I"].items()), "reported")
save_tsv(mp, "moran_perm.tsv")
save_tsv(mor, "moran.tsv")
save_tsv(gea, "geary.tsv")

# invariances, every gene: I(-y) is exact in IEEE arithmetic; I(3.7y + 112.5) rounds each element once
with Phase("invariances"):
    neg = b.copy(); neg.X = -b.X
    In = sq.gr.spatial_autocorr(neg, connectivity_key="w_connectivities", genes=genes, mode="moran", copy=True, show_progress_bar=False).loc[genes, "I"].to_numpy()
    Ia = np.empty(len(genes))
    for s in range(0, len(genes), 2000):
        sub = ad.AnnData(X=3.7 * b.X[:, s:s + 2000].toarray() + 112.5, obs=b.obs[[]])
        sub.var_names = genes[s:s + 2000]
        sub.obsp["w_connectivities"] = b.obsp["w_connectivities"]
        Ia[s:s + 2000] = sq.gr.spatial_autocorr(sub, connectivity_key="w_connectivities", genes=genes[s:s + 2000], mode="moran", copy=True,
                                                show_progress_bar=False).loc[genes[s:s + 2000], "I"].to_numpy()
I0 = mor["I"].to_numpy()
rec(f"I(y) vs I(-y), all {len(genes)} genes", f"max |diff| {np.abs(In - I0).max():.3e}", "== 0 exactly", bool(np.array_equal(In, I0)))
with Phase("affine bound"):
    Bg = affine_bound(W, b.X, I0)
ratio = np.abs(Ia - I0) / Bg
rec(f"I(y) vs I(3.7y + 112.5), all {len(genes)} genes", f"max |diff| {np.abs(Ia - I0).max():.3e}; max |diff| / per-gene rounding bound {ratio.max():.3f} "
    f"(bounds {Bg.min():.1e} to {Bg.max():.1e})", "<= 1 on every gene", finite_le(ratio.max(), 1.0))

# planted gene on the real positions: a wave along x plus noise, and a noise-only control
rng = np.random.default_rng(20261020)
L = (xy[:, 0].max() - xy[:, 0].min()) / 4
b.obs["planted_wave"] = np.sin(2 * np.pi * xy[:, 0] / L) + rng.normal(0, 1, n)
b.obs["planted_noise"] = rng.normal(0, 1, n)
pl = sq.gr.spatial_autocorr(b, connectivity_key="w_connectivities", attr="obs", genes=["planted_wave", "planted_noise"], mode="moran",
                            copy=True, show_progress_bar=False)
zw = (pl.loc["planted_wave", "I"] + 1 / (n - 1)) / np.sqrt(pl.loc["planted_wave", "var_norm"])
zn = (pl.loc["planted_noise", "I"] + 1 / (n - 1)) / np.sqrt(pl.loc["planted_noise", "var_norm"])
rec("planted wave / noise-only gene, z", f"{zw:+.1f} / {zn:+.2f}", "> 5 / |z| < 5", bool(zw > 5 and abs(zn) < 5))

# ------------------------------------------------------------------ done
pd.DataFrame(ROWS, columns=["check", "value", "bound", "status"]).to_csv(os.path.join(OUT, "score.tsv"), sep="\t", index=False)
pd.DataFrame(PHASES, columns=["phase", "seconds"]).to_csv(os.path.join(OUT, "phases.tsv"), sep="\t", index=False)
log(f"total {time.time() - T0:.1f} s; maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20:.2f} GiB")
if FAILED:
    log("FAILED:", "; ".join(FAILED))
    sys.exit(1)
log("SQUIDPY REAL OK")
