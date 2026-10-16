#!/usr/bin/env python3
"""Squidpy on Graviton4: spatial graphs, neighbourhood enrichment and autocorrelation,
each held to an exact identity or a planted truth.

Everything is synthesised from one seed, so nothing is fetched at run time:

 1. Graphs. spatial_neighbors_knn must equal a brute-force numpy kNN (pattern AND distances),
    spatial_neighbors_radius a brute-force radius graph, and spatial_neighbors_grid on a hex
    lattice must give every interior spot exactly 3k(k+1) neighbours for k rings (6, 18, 36).
 2. Neighbourhood enrichment. Its count matrix is M' A M for one-hot labels M and adjacency A,
    so it is checked as integers. A PLANTED layout -- A and B mixed on the left half, C alone
    on the right -- must come back with A-B enriched and A-C, B-C depleted, each beyond 5
    sigma; the SAME cells with shuffled labels must show nothing beyond 5 sigma.
 3. Moran's I and Geary's C equal their closed forms on the same weights, and so do the
    analytic variances under normality (Geary's was corrected in 1.8.2). I is invariant to
    y -> -y (exactly) and y -> 3.7y + 112.5 (to rounding); a planted spatial gene is
    autocorrelated beyond 5 sigma and a random gene is not.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import sys
import warnings

import numpy as np

ROWS = []


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def ver(label, fn):
    """A descriptive line must never be able to fail the run."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            die("%s is not finite" % name)


warnings.simplefilter("ignore")
import anndata as ad
import pandas as pd
import scipy.sparse as sp
import squidpy as sq

ver("squidpy", lambda: sq.__version__)
ver("anndata", lambda: ad.__version__)
ver("scanpy", lambda: __import__("scanpy").__version__)
ver("numpy", lambda: np.__version__)
ver("leidenalg", lambda: getattr(__import__("leidenalg"), "__version__", "unknown"))

SEED = 20261016
rng = np.random.default_rng(SEED)
N, K = 2000, 6

# Continuous uniform positions: no two pairwise distances tie, so "the k nearest" is unique and
# the graph has exactly one right answer.
xy = rng.uniform(0, 100, size=(N, 2))
left = xy[:, 0] < 50
labels = np.where(left, np.where(rng.random(N) < 0.5, "A", "B"), "C")
y_struct = np.sin(2 * np.pi * xy[:, 0] / 50) + 0.3 * rng.normal(size=N)
y_rand = rng.normal(size=N)
X = np.column_stack([y_struct, y_rand, 3.7 * y_struct + 112.5, -y_struct])
genes = ["struct", "rand", "struct_affine", "struct_neg"]
A = ad.AnnData(X, obs=pd.DataFrame({"ct": pd.Categorical(labels)}, index=["c%d" % i for i in range(N)]),
               var=pd.DataFrame(index=genes))
A.obsm["spatial"] = xy
rec("cells", "%d cells, %d A / %d B / %d C" % (N, (labels == "A").sum(), (labels == "B").sum(), (labels == "C").sum()))

d2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1)
np.fill_diagonal(d2, np.inf)

# ======================================================== 1a. kNN graph == brute force
sq.gr.spatial_neighbors_knn(A, n_neighs=K)
C = A.obsp["spatial_connectivities"]
D = A.obsp["spatial_distances"]
nb = np.argsort(d2, axis=1, kind="stable")[:, :K]
Kb = sp.csr_matrix((np.ones(N * K), (np.repeat(np.arange(N), K), nb.ravel())), shape=(N, N))
pat = int(abs(Kb - (C > 0).astype("float64")).sum())
dd = float(np.max(np.abs(np.take_along_axis(D.toarray(), nb, 1) - np.sqrt(np.take_along_axis(d2, nb, 1)))))
rec("knn_graph", "%d edges; pattern differs from brute force in %d entries, distances by %.3e" % (C.nnz, pat, dd))
# A graph is a set of edges: either it is the brute-force set or it is wrong. Distances are one
# sqrt of the same float64 sum of squares, so 1e-12 is round-off headroom, not a tolerance.
if C.nnz != N * K or pat != 0:
    die("spatial_neighbors_knn is not the k=%d nearest-neighbour graph (%d entries differ)" % (K, pat))
finite("kNN distances", dd)
if dd > 1e-12:
    die("kNN distances are %.3e from Euclidean" % dd)

# ===================================================== 1b. radius graph == brute force
R_ = 4.0
sq.gr.spatial_neighbors_radius(A, radius=R_, key_added="rad")
R = A.obsp["rad_connectivities"]
Rb = sp.csr_matrix(d2 <= R_ * R_).astype("int64")
rpat = int(abs(Rb - (R > 0).astype("int64")).sum())
rsym = int(abs((R > 0).astype("int64") - (R > 0).astype("int64").T).sum())
rec("radius_graph", "%d edges within r=%g; differs from brute force in %d, asymmetric in %d" % (R.nnz, R_, rpat, rsym))
if rpat != 0 or rsym != 0:
    die("spatial_neighbors_radius is not the brute-force radius graph (%d differ, %d asymmetric)" % (rpat, rsym))

# ================================== 1c. hex grid: interior degree is exactly 3k(k+1)
nr = nc = 30
hexy = np.array([(c + 0.5 * (r % 2), r * np.sqrt(3) / 2) for r in range(nr) for c in range(nc)]) * 100.0
H = ad.AnnData(np.zeros((len(hexy), 1)))
H.obsm["spatial"] = hexy
rr, cc = np.divmod(np.arange(len(hexy)), nc)
degs = []
for rings in (1, 2, 3):
    sq.gr.spatial_neighbors_grid(H, n_neighs=6, n_rings=rings, key_added="g%d" % rings)
    G = H.obsp["g%d_connectivities" % rings]
    deg = np.asarray((G > 0).sum(1)).ravel()
    interior = (rr >= rings) & (rr < nr - rings) & (cc >= rings) & (cc < nc - rings)
    want = 3 * rings * (rings + 1)
    got = sorted(set(deg[interior].tolist()))
    degs.append("%d rings: %s (want %d) over %d interior spots" % (rings, got, want, int(interior.sum())))
    if got != [want] or int(deg.max()) != want or float(G.diagonal().sum()) != 0.0:
        die("hex grid with %d rings: interior degrees %s, max %d, expected exactly %d and no self-loops"
            % (rings, got, int(deg.max()), want))
rec("hex_grid_degree", "; ".join(degs))

# ======================== 2. neighbourhood enrichment: exact counts, planted signs, a control
res = sq.gr.nhood_enrichment(A, cluster_key="ct", connectivity_key="rad", n_perms=1000, seed=0,
                             copy=True, n_jobs=1, show_progress_bar=False)
z, cnt = np.asarray(res.zscore, dtype="float64"), np.asarray(res.counts, dtype="int64")
cats = list(A.obs["ct"].cat.categories)
M = np.stack([(A.obs["ct"].to_numpy() == c).astype("int64") for c in cats], axis=1)
cnt_np = M.T @ (R > 0).astype("int64") @ M
rec("nhood_counts", "squidpy %s, M'AM %s" % (cnt.tolist(), np.asarray(cnt_np).tolist()))
if not np.array_equal(cnt, np.asarray(cnt_np)) or int(cnt.sum()) != int((R > 0).sum()):
    die("nhood_enrichment counts are not M'AM, or do not sum to the edge count")
finite("nhood z-scores", z)
ia, ib, ic = cats.index("A"), cats.index("B"), cats.index("C")
zab, zac, zbc = z[ia, ib], z[ia, ic], z[ib, ic]
rec("nhood_planted_z", "A-B %+.2f, A-C %+.2f, B-C %+.2f, C-C %+.2f" % (zab, zac, zbc, z[ic, ic]))
# The z-score is in units of the permutation null's own sd, so 5 is 5 sigma by construction --
# the bound is the null, not a margin over what this run happened to give.
if not (zab > 5 and zac < -5 and zbc < -5):
    die("planted layout not recovered: A-B %+.2f (want > +5), A-C %+.2f, B-C %+.2f (want < -5)" % (zab, zac, zbc))
A.obs["shuffled"] = pd.Categorical(np.random.default_rng(SEED + 1).permutation(labels))
z0 = np.asarray(sq.gr.nhood_enrichment(A, cluster_key="shuffled", connectivity_key="rad", n_perms=1000,
                                       seed=0, copy=True, n_jobs=1, show_progress_bar=False).zscore, dtype="float64")
finite("shuffled z-scores", z0)
rec("control_shuffled_z", "max |z| %.2f over the same cells and graph, labels permuted" % float(np.max(np.abs(z0))))
if float(np.max(np.abs(z0))) > 5:
    die("shuffled labels gave |z| = %.2f; with no structure nothing should pass 5 sigma" % float(np.max(np.abs(z0))))

# ======================================= 3. Moran's I and Geary's C against closed forms
W = C.toarray().astype("float64")
W = W / W.sum(1, keepdims=True)        # transformation=True is row-normalisation (l1)
n = float(N)
s0 = W.sum()
s1 = 0.5 * ((W + W.T) ** 2).sum()
s2 = ((W.sum(1) + W.sum(0)) ** 2).sum()


def moran(y):
    zc = y - y.mean()
    return n / s0 * (zc @ W @ zc) / (zc @ zc)


def geary(y):
    zc = y - y.mean()
    return (n - 1) * np.sum(W * (y[:, None] - y[None, :]) ** 2) / (2 * s0 * (zc @ zc))


EI = -1.0 / (n - 1)
VI = (n * n * s1 - n * s2 + 3 * s0 * s0) / ((n - 1) * (n + 1) * s0 * s0) - EI ** 2
VC = ((2 * s1 + s2) * (n - 1) - 4 * s0 * s0) / (2 * (n + 1) * s0 * s0)
mi = sq.gr.spatial_autocorr(A, connectivity_key="spatial_connectivities", mode="moran", copy=True,
                            n_jobs=1, show_progress_bar=False)
gc = sq.gr.spatial_autocorr(A, connectivity_key="spatial_connectivities", mode="geary", copy=True,
                            n_jobs=1, show_progress_bar=False)
I = {g: float(mi.loc[g, "I"]) for g in genes}
Cg = {g: float(gc.loc[g, "C"]) for g in genes}
finite("Moran's I / Geary's C", list(I.values()), list(Cg.values()), mi["var_norm"], gc["var_norm"])
dI = max(abs(I[g] - moran(X[:, j])) for j, g in enumerate(genes))
dC = max(abs(Cg[g] - geary(X[:, j])) for j, g in enumerate(genes))
rec("moran_vs_closed_form", "max |diff| %.3e over %d genes" % (dI, len(genes)))
rec("geary_vs_closed_form", "max |diff| %.3e" % dC)
# Two float64 routes to one statistic on the same weights: conditioning x eps, set at 1e-10.
if dI > 1e-10 or dC > 1e-10:
    die("Moran/Geary disagree with the closed form: %.3e / %.3e" % (dI, dC))
vI, vC = float(mi["var_norm"].iloc[0]), float(gc["var_norm"].iloc[0])
rvI, rvC = abs(vI - VI) / VI, abs(vC - VC) / VC
rec("var_norm_moran", "squidpy %.10e, closed form %.10e, rel %.2e" % (vI, VI, rvI))
rec("var_norm_geary", "squidpy %.10e, closed form %.10e, rel %.2e" % (vC, VC, rvC))
# squidpy forms S0..S2 from the float32 connectivities, so its variance carries float32
# round-off (eps 1.2e-7); 1e-5 leaves summation headroom and is ~10x below the gap between the
# two variances (Moran's vs Geary's differ by 15% here), which is the 1.8.2 bug's signature.
if rvI > 1e-5 or rvC > 1e-5:
    die("analytic variances disagree with Cliff & Ord: Moran rel %.2e, Geary rel %.2e" % (rvI, rvC))
if abs(vC - VI) / VI < 1e-3:
    die("Geary's variance equals Moran's -- the pre-1.8.2 bug")
d_neg = abs(I["struct"] - I["struct_neg"])
d_aff = abs(I["struct"] - I["struct_affine"])
rec("moran_invariance", "I(y) - I(-y) = %.3e, I(y) - I(3.7y+112.5) = %.3e" % (d_neg, d_aff))
# Negation is exact in IEEE arithmetic and every product z_i z_j keeps its value, so -y must be
# bit-identical. The shift by 112.5 is rounded once per element (112.5 x 2.2e-16 ~ 2.5e-14 on
# unit-scale data) before centring, so that one carries rounding: bound 1e-12.
if d_neg != 0.0:
    die("I(-y) != I(y) (%.3e); negation is exact" % d_neg)
if d_aff > 1e-12:
    die("Moran's I not affine-invariant: %.3e" % d_aff)
sd = np.sqrt(VI)
zs, zr = (I["struct"] - EI) / sd, (I["rand"] - EI) / sd
rec("moran_planted", "struct I %.4f (z %+.1f), random I %.4f (z %+.2f), E[I] %.6f" % (I["struct"], zs, I["rand"], zr, EI))
if not (zs > 5 and abs(zr) < 5):
    die("planted spatial gene z %.1f (want > 5), random gene z %.2f (want |z| < 5)" % (zs, zr))

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k_, v in ROWS:
        fh.write("%s\t%s\n" % (k_, v))

print("SQUIDPY IDENTITIES OK")
