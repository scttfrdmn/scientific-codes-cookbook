#!/usr/bin/env python3
"""scvelo-real: the whole Pijuan-Sala 2019 mouse gastrulation atlas through scVelo, checked as it goes.

    python3 -u real.py <gastrulation.h5ad> <out dir> <n_jobs> [max_dynamical_genes]

The workflow is scVelo's own tutorial order: filter_genes(min_shared_counts=20) ->
normalize_per_cell -> log1p + 2,000 HVGs -> PCA (30) -> kNN (30) -> moments -> steady-state and
stochastic velocity -> recover_dynamics on the velocity genes (n_jobs workers) -> dynamical
velocity -> velocity_graph. `max_dynamical_genes` caps the dynamical fit (a sizing probe only).

What must hold, asserted (each a property of the computation, not a band on this run):
  * the counts are non-negative integers, and the initial sizes scVelo records are their row sums;
  * normalize_per_cell scales each cell by ONE factor, median(initial size) / initial size, per
    layer, and leaves the sparsity pattern alone;
  * Ms / Mu are the mean over each cell and its graph neighbours of the normalised counts;
  * steady-state gamma is the extreme-quantile least-squares slope, recomputed in numpy, and the
    steady-state and stochastic velocities are exactly Mu - gamma * Ms;
  * the dynamical fit is per gene, so fitting a subset of genes again in ONE process gives the
    same rates bit for bit as the n_jobs-worker fit (worker count cannot change a result);
  * dynamical residual velocity is beta * Mu - gamma * Ms from the fitted rates.
Everything else -- likelihoods, rates, how many genes pass -- is a path and is reported.

The pandas-3 workarounds are the recipe's (recipes/scvelo): make_unique_list, t_max=False,
mode="dynamical_residuals"; each documented call is probed first and recorded.
"""
import hashlib
import json
import os
import resource
import signal
import sys
import time
import warnings

import numpy as np

T0 = time.time()
ROWS, PHASES = [], []
H5AD, OUT, NJ = sys.argv[1], sys.argv[2], int(sys.argv[3])
MAXG = int(sys.argv[4]) if len(sys.argv) > 4 else 0
os.makedirs(OUT, exist_ok=True)
SEED = 0


def rec(k, v):
    """Always str() the value -- `"%s" % obj` unpacks a tuple-like object as format args."""
    v = v if isinstance(v, str) else str(v)
    print("%s\t%s" % (k, v), flush=True)
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg, flush=True)
    dump()
    sys.exit(1)


def ok(cond, msg):
    """numpy booleans: `if not bool(c)`, never `c is False`."""
    if not bool(cond):
        die(msg)


def finite(name, *vals):
    """`nan > tol` is False, so a one-sided bound cannot catch a NaN; this can."""
    for v in vals:
        if not np.all(np.isfinite(np.asarray(v, dtype="float64"))):
            die("%s is not finite" % name)


def ver(label, fn):
    try:
        rec(label, fn())
    except Exception as exc:  # noqa: BLE001 -- a description must never fail the run
        rec(label, "unknown (%s)" % type(exc).__name__)


class ProbeTimeout(Exception):
    pass


def _alarm(signum, frame):
    raise ProbeTimeout("no result within the probe's time limit")


def probe(label, fn, limit=None):
    """Run a call that is recorded, never asserted. `limit` (s) bounds it with SIGALRM, which fires
    between Python-level iterations -- scipy's ARPACK loop is one -- so a solve that does not
    converge is reported instead of holding the run to its TTL."""
    t = time.time()
    if limit:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(int(limit))
    try:
        fn()
        rec(label, "works (%.1f s)" % (time.time() - t))
        return None
    except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
        rec(label, "raises %s after %.1f s: %s" % (type(exc).__name__, time.time() - t,
                                                    (str(exc).splitlines() or [""])[0][:120]))
        return exc
    finally:
        if limit:
            signal.alarm(0)


class phase:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()

    def __exit__(self, *a):
        dt = time.time() - self.t
        PHASES.append((self.name, dt))
        print("phase\t%s\t%.1f s\trss %.2f GiB" % (self.name, dt, maxrss()), flush=True)


def maxrss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # KiB on Linux


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def dump():
    with open(os.path.join(OUT, "score.tsv"), "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in ROWS:
            fh.write("%s\t%s\n" % (k, v))
    with open(os.path.join(OUT, "phases.tsv"), "w") as fh:
        fh.write("phase\tseconds\n")
        for k, v in PHASES:
            fh.write("%s\t%.2f\n" % (k, v))


warnings.simplefilter("ignore")
import anndata as ad
import pandas as pd
import scanpy as sc
import scipy
import scipy.sparse as sp
import scvelo as scv
from scipy.stats import spearmanr

scv.settings.verbosity = 1
for name, mod in (("scvelo", scv), ("scanpy", sc), ("anndata", ad), ("pandas", pd), ("numpy", np), ("scipy", scipy)):
    ver(name, lambda m=mod: getattr(m, "__version__", "unknown"))
ver("n_jobs", lambda: NJ)

# ============================================================ load + the counts themselves
with phase("read_h5ad"):
    A = ad.read_h5ad(H5AD)
A.var_names_make_unique()
ver("dataset", lambda: "%d cells x %d genes; X %s; layers %s" % (A.n_obs, A.n_vars, type(A.X).__name__, ", ".join(
    "%r: %s %s" % (k, type(v).__name__, getattr(v, "dtype", "?")) for k, v in A.layers.items())))
ver("obs_columns", lambda: ",".join(map(str, A.obs.columns)))
ver("uns_keys", lambda: ",".join(map(str, A.uns.keys())))
ok("spliced" in A.layers and "unspliced" in A.layers, "no spliced/unspliced layers")


def csr(x):
    return x.tocsr() if sp.issparse(x) else sp.csr_matrix(x)


with phase("raw_checks"):
    S0, U0 = csr(A.layers["spliced"]), csr(A.layers["unspliced"])
    for nm, M in (("spliced", S0), ("unspliced", U0)):
        d = M.data
        ok(d.size == 0 or (d.min() >= 0 and np.all(d == np.round(d))), "%s is not non-negative integer counts" % nm)
    ns, nu = float(S0.sum(dtype="float64")), float(U0.sum(dtype="float64"))
    rec("raw_counts", "spliced %d (nnz %d), unspliced %d (nnz %d), unspliced fraction %.4f"
        % (ns, S0.nnz, nu, U0.nnz, nu / (ns + nu)))
    for k_, v_ in A.layers.items():
        if k_ not in ("spliced", "unspliced"):
            ver("layer_%s" % k_, lambda v_=v_: "sum %d, nnz %d" % (float(csr(v_).sum(dtype="float64")), csr(v_).nnz))
    X0 = csr(A.X)
    xs = float(X0.sum(dtype="float64"))
    rel = {"X == spliced": (X0 != S0).nnz == 0, "X == spliced + unspliced": (X0 != (S0 + U0)).nnz == 0}
    rec("X_relation", "; ".join("%s: %s" % (k, bool(v)) for k, v in rel.items()) + "; sum(X) %.0f" % xs)
    rs_s = np.asarray(S0.sum(1, dtype="float64")).ravel()
    rs_u = np.asarray(U0.sum(1, dtype="float64")).ravel()
    rec("cells_with_zero_counts", "spliced %d, unspliced %d" % (int((rs_s == 0).sum()), int((rs_u == 0).sum())))
    del X0

# ================================================ filter + normalise, and the one-factor identity
with phase("filter_genes"):
    scv.pp.filter_genes(A, min_shared_counts=20)
rec("filter_genes", "min_shared_counts=20 keeps %d of %d genes" % (A.n_vars, S0.shape[1]))
for nm, rs in (("spliced", rs_s), ("unspliced", rs_u)):
    got = A.obs["initial_size_%s" % nm].to_numpy(dtype="float64")
    ok(np.array_equal(got, rs), "initial_size_%s is not the raw row sum (max diff %.3g)" % (nm, np.max(np.abs(got - rs))))
tot = A.obs["initial_size_spliced"].to_numpy("float64") + A.obs["initial_size_unspliced"].to_numpy("float64")
ok(np.array_equal(tot, rs_s + rs_u), "spliced + unspliced initial sizes do not add to the cell's molecules")
ok(tot.sum() == ns + nu, "per-cell totals do not add up to the dataset total")
rec("initial_sizes", "== raw row sums exactly, both layers; spliced+unspliced sums to %d molecules" % (ns + nu))
del S0, U0
Sraw, Uraw = csr(A.layers["spliced"]).copy(), csr(A.layers["unspliced"]).copy()

with phase("normalize_per_cell"):
    scv.pp.normalize_per_cell(A)
for nm, R in (("spliced", Sraw), ("unspliced", Uraw)):
    N = csr(A.layers[nm])
    ok(np.array_equal(N.indptr, R.indptr) and np.array_equal(N.indices, R.indices),
       "normalize_per_cell changed the %s sparsity pattern" % nm)
    init = A.obs["initial_size_%s" % nm].to_numpy("float64")
    med = np.median(init)
    med += med == 0
    f = np.where(init > 0, med / np.where(init > 0, init, 1), 1.0)
    rows = np.repeat(np.arange(N.shape[0]), np.diff(N.indptr))
    r = np.abs(N.data.astype("float64") - R.data.astype("float64") * f[rows]) / (R.data.astype("float64") * f[rows])
    finite("normalised %s" % nm, N.data)
    rel = float(r.max()) if r.size else 0.0
    rec("one_factor_%s" % nm, "every entry == raw x median(%d)/initial_size, max rel %.2e over %d entries"
        % (med, rel, r.size))
    # float32 storage: eps 6e-8 per operation, two operations
    ok(rel < 1e-6, "normalize_per_cell is not one factor per cell on %s (rel %.3e)" % (nm, rel))
del Sraw, Uraw

with phase("log1p_hvg"):
    sc.pp.log1p(A)
    sc.pp.highly_variable_genes(A, n_top_genes=2000, flavor="seurat", subset=True)
rec("hvg", "%d genes; sha %s" % (A.n_vars, hashlib.sha256("\n".join(A.var_names).encode()).hexdigest()[:16]))
with open(os.path.join(OUT, "hvg.txt"), "w") as fh:
    fh.write("\n".join(A.var_names) + "\n")

# ============================================================== graph + moments identity
with phase("pca"):
    sc.pp.pca(A, n_comps=30, svd_solver="arpack", random_state=SEED)
with phase("neighbors"):
    sc.pp.neighbors(A, n_neighbors=30, n_pcs=30, random_state=SEED)
with phase("moments"):
    scv.pp.moments(A, n_pcs=None, n_neighbors=None)
Cn = (A.obsp["connectivities"] > 0).tolil()
Cn.setdiag(True)
Cn = Cn.tocsr().astype("float64")
k = np.asarray(Cn.sum(1)).ravel()
Ms = np.asarray(A.layers["Ms"], dtype="float64")
Mu = np.asarray(A.layers["Mu"], dtype="float64")
finite("moments", Ms, Mu)
cs = np.clip(Ms.max(0), 1e-12, None)
cu = np.clip(Mu.max(0), 1e-12, None)
dMs = float(np.max(np.abs((Cn @ csr(A.layers["spliced"])).toarray() / k[:, None] - Ms) / cs))
dMu = float(np.max(np.abs((Cn @ csr(A.layers["unspliced"])).toarray() / k[:, None] - Mu) / cu))
rec("moments", "Ms rel %.2e, Mu rel %.2e; %d-%d cells per neighbourhood (mean %.1f)"
    % (dMs, dMu, int(k.min()), int(k.max()), k.mean()))
# float32 moments (eps 1.2e-7) over up to ~100 terms; 1e-6 is that plus summation headroom
ok(dMs < 1e-6 and dMu < 1e-6, "moments are not neighbourhood means (rel %.3e / %.3e)" % (dMs, dMu))
rec("graph", "%d edges, sha %s" % (A.obsp["connectivities"].nnz, sha(A.obsp["connectivities"].indices)))
rec("Ms_sha", sha(A.layers["Ms"]))

# ===================================== steady-state + stochastic velocity: gamma and velocity exactly
with phase("velocity_deterministic"):
    scv.tl.velocity(A, mode="deterministic")
g_sc = A.var["velocity_gamma"].to_numpy(dtype="float64")
# The cells a gene is fitted on are DEFINED by scVelo's own float32 arithmetic (LinearRegression.
# _trim_data: Ms/max + Mu/max >= its 95th percentile, all in float32), so the selection is rebuilt
# in float32 the same way; the slope over it is then recomputed independently in float64.
M32 = np.array([np.asarray(A.layers["Ms"]), np.asarray(A.layers["Mu"])])
nd32 = np.sum(M32 / M32.max(axis=1, keepdims=True).clip(1e-3, None), axis=0)
sel = nd32 >= np.percentile(nd32, 95, axis=0)
del M32, nd32
nrm64 = Ms / np.clip(Ms.max(0), 1e-3, None) + Mu / np.clip(Mu.max(0), 1e-3, None)
sel64 = nrm64 >= np.percentile(nrm64, 95, axis=0)
flip = int((sel != sel64).any(0).sum())
del nrm64, sel64
den = (Ms * Ms * sel).sum(0)
g_np = np.where(den > 0, (Ms * Mu * sel).sum(0) / np.where(den > 0, den, 1), 0.0)
finite("steady-state gamma", g_sc, g_np)
pos = np.abs(g_np) > 0
nsel = sel.sum(0)
rel_g = np.abs(g_sc[pos] - g_np[pos]) / np.abs(g_np[pos])
# scVelo sums n positive float32 products per gene: each sum is within n*u of exact (u = 2^-24),
# so the ratio is within 2(n+1)u -- a per-gene worst-case bound, not a band on this run.
tol = 2.0 * (nsel[pos] + 1) * 2.0**-24
dg = float(rel_g.max())
rec("ss_gamma_vs_numpy", "max rel %.2e over %d genes, worst rel/bound %.3f (bound 2(n+1)u, n = %d-%d selected cells); "
    "%d genes would select different cells in float64 (%d with no signal, both 0: %s)"
    % (dg, int(pos.sum()), float((rel_g / tol).max()), int(nsel.min()), int(nsel.max()), flip,
       int((~pos).sum()), bool(np.all(g_sc[~pos] == 0))))
ok(np.all(rel_g <= tol) and np.all(g_sc[~pos] == 0),
   "steady-state gamma is not the extreme-quantile slope (rel %.3e, %.2fx its float32 bound)" % (dg, float((rel_g / tol).max())))
dv = float(np.max(np.abs(A.layers["velocity"] - (A.layers["Mu"] - A.var["velocity_gamma"].to_numpy() * A.layers["Ms"]))))
rec("ss_velocity_identity", "max |velocity - (Mu - gamma Ms)| %.3e" % dv)
ok(dv == 0.0, "steady-state velocity is not Mu - gamma*Ms (%.3e)" % dv)
vg_det = A.var["velocity_genes"].to_numpy(bool)
rec("velocity_genes_deterministic", "%d of %d" % (int(vg_det.sum()), A.n_vars))

# scv.tl.velocity's DEFAULT mode. Probe it as documented first; on numpy 2.5 its no-offset
# least-squares loop assigns a shape-(1,) array to a scalar (optimization.py:184) and raises.
import scvelo.tools.optimization as sopt

svel = sys.modules["scvelo.tools.velocity"]   # `scvelo.tools.velocity` the attribute is the function

err_st = probe("probe_velocity_stochastic_default", lambda: scv.tl.velocity(A[:, :200].copy()))
if isinstance(err_st, ValueError):
    _orig_lsq = sopt.leastsq_generalized

    def _lsq_generalized(x, y, x2, y2, res_std=None, res2_std=None, fit_offset=False, fit_offset2=False, perc=None):
        """scVelo's own no-offset branch with `.item()` on the 1x1 solve -- the same arithmetic."""
        if fit_offset or fit_offset2:
            return _orig_lsq(x, y, x2, y2, res_std, res2_std, fit_offset, fit_offset2, perc)
        if perc is not None:
            if isinstance(perc, (list, tuple)):
                perc = perc[1]
            w = sp.csr_matrix(sopt.get_weight(x, y, perc=perc) | sopt.get_weight(x, perc=perc)).astype(bool)
            x, y = w.multiply(x).tocsr(), w.multiply(y).tocsr()
        n_obs, n_var = x.shape
        offset, offset_ss = np.zeros(n_var, dtype="float32"), np.zeros(n_var, dtype="float32")
        gamma = np.ones(n_var, dtype="float32")
        if (res_std is None) or (res2_std is None):
            res_std, res2_std = np.ones(n_var), np.ones(n_var)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            x, y = (np.vstack((sopt.make_dense(x) / res_std, x2 / res2_std)),
                    np.vstack((sopt.make_dense(y) / res_std, y2 / res2_std)))
        for i in range(n_var):
            Ai = np.c_[x[:, i]]
            gamma[i] = np.linalg.pinv(Ai.T.dot(Ai)).dot(Ai.T.dot(y[:, i])).item()
        gamma[np.isnan(gamma)] = 0
        return offset, offset_ss, gamma
    svel.leastsq_generalized = _lsq_generalized
    rec("workaround_stochastic", "leastsq_generalized no-offset branch with .item() (numpy 2.5 refuses array->scalar)")

with phase("velocity_stochastic"):
    scv.tl.velocity(A, mode="stochastic", vkey="stoch")
g_st = A.var["stoch_gamma"].to_numpy(dtype="float64")
finite("stochastic gamma", g_st)
dvs = float(np.max(np.abs(A.layers["stoch"] - (A.layers["Mu"] - A.var["stoch_gamma"].to_numpy() * A.layers["Ms"]))))
rec("stoch_velocity_identity", "max |velocity - (Mu - gamma Ms)| %.3e" % dvs)
ok(dvs == 0.0, "stochastic velocity is not Mu - gamma*Ms (%.3e)" % dvs)
rec("stoch_vs_ss_gamma", "Spearman %.4f over %d genes; %d stochastic velocity genes"
    % (spearmanr(g_st, g_sc)[0], A.n_vars, int(A.var["stoch_genes"].sum())))

# =========================================================== dynamical model, n_jobs workers
import scvelo.tools._em_model_core as emc

err = probe("probe_recover_dynamics_default",
            lambda: scv.tl.recover_dynamics(A[:, :2].copy(), var_names="all", n_jobs=1, show_progress_bar=False))
if isinstance(err, TypeError):
    _orig = emc.make_unique_list

    def _unique_list(key, allow_array=False):
        if isinstance(key, (list, tuple)) and all(isinstance(x, str) for x in key):
            return list(dict.fromkeys(key))
        return _orig(key, allow_array=allow_array)
    emc.make_unique_list = _unique_list
    rec("workaround_unique", "make_unique_list -> dict.fromkeys (pandas.unique rejects a list)")

genes = list(A.var_names[vg_det])
if MAXG and len(genes) > MAXG:
    genes = list(np.random.default_rng(20261020).choice(genes, MAXG, replace=False))
rec("dynamical_genes", "%d (the steady-state velocity genes%s)" % (len(genes), ", sampled" if MAXG else ""))
with phase("recover_dynamics"):
    scv.tl.recover_dynamics(A, var_names=genes, t_max=False, n_jobs=NJ, show_progress_bar=False)
fit = A.var.loc[genes]
cols = ["fit_alpha", "fit_beta", "fit_gamma", "fit_t_", "fit_scaling", "fit_likelihood"]
fitted = fit["fit_alpha"].notna().to_numpy()
rec("dynamical_fitted", "%d of %d genes recoverable" % (int(fitted.sum()), len(genes)))
ok(fitted.sum() > 0, "no gene was fitted")
F = fit.loc[fitted, cols].to_numpy("float64")
finite("dynamical rates", F)
L = fit.loc[fitted, "fit_likelihood"].to_numpy("float64")
rec("likelihood_quantiles", "min %.3g, q25 %.3g, median %.3g, q75 %.4f, max %.4f; %d of %d above scVelo's 0.001 cut"
    % (tuple(np.quantile(L, [0, .25, .5, .75, 1])) + (int((L > 1e-3).sum()), L.size)))
fit.to_csv(os.path.join(OUT, "fit_var.tsv"), sep="\t", float_format="%.17g")

# worker count cannot change a per-gene fit: refit some genes in one process, compare bit for bit
sub = [g for g, f in zip(genes, fitted) if f][:min(16, int(fitted.sum()))]
B = A[:, sub].copy()
for c in list(B.var.columns):
    if c.startswith("fit_"):
        del B.var[c]
for l in ("fit_t", "fit_tau", "fit_tau_"):
    if l in B.layers:
        del B.layers[l]
if "loss" in B.varm:
    del B.varm["loss"]
with phase("recover_dynamics_serial_subset"):
    scv.tl.recover_dynamics(B, var_names=sub, t_max=False, n_jobs=1, show_progress_bar=False)
P1 = A.var.loc[sub, cols].to_numpy("float64")
P2 = B.var.loc[sub, cols].to_numpy("float64")
same = np.array_equal(P1, P2)
dT = float(np.nanmax(np.abs(np.asarray(A[:, sub].layers["fit_t"]) - np.asarray(B.layers["fit_t"]))))
rec("njobs_invariance", "%d genes refit with n_jobs=1 vs n_jobs=%d: rates+likelihood identical %s, max |dfit_t| %.3e"
    % (len(sub), NJ, same, dT))
ok(same and dT == 0.0, "the per-gene fit depends on the worker count")
del B

probe("probe_velocity_dynamical", lambda: scv.tl.velocity(A[:, sub].copy(), mode="dynamical"))
with phase("velocity_dynamical_residuals"):
    scv.tl.velocity(A, mode="dynamical_residuals", vkey="dyn")
gs = ~np.isnan(A.var["fit_alpha"].to_numpy("float64"))
Vd = np.asarray(A.layers["dyn"])[:, gs].astype("float64")
fb = A.var["fit_beta"].to_numpy("float64")[gs]
fg = A.var["fit_gamma"].to_numpy("float64")[gs]
fsc = A.var["fit_scaling"].to_numpy("float64")[gs]
Vnp = (Mu[:, gs] / fsc) * (fb * fsc) - Ms[:, gs] * fg
finite("dynamical velocity", Vd)
dd = float(np.max(np.abs(Vd - Vnp)) / np.max(np.abs(Vnp)))
rec("dyn_velocity_identity", "max |v - (beta Mu - gamma Ms)| / max|v| %.2e over %d genes" % (dd, int(gs.sum())))
# float32 Ms/Mu, float64 rates: round-off only
ok(dd < 1e-6, "dynamical residual velocity is not beta*Mu - gamma*Ms (rel %.3e)" % dd)
rec("dyn_velocity_genes", "%d" % int(A.var["dyn_genes"].sum()))
rec("dyn_vs_ss_gamma_over_beta", "Spearman(fit_gamma/fit_beta, steady-state gamma) %.4f"
    % spearmanr(fg / fb, g_sc[gs])[0])

# ================================================ the standard downstream call, recorded only
with phase("velocity_graph_stochastic"):
    probe("probe_velocity_graph", lambda: scv.tl.velocity_graph(A, vkey="stoch", n_jobs=NJ, show_progress_bar=False),
          limit=900)
if "stoch_graph" in A.uns:
    rec("velocity_graph", "%d edges, sha %s" % (A.uns["stoch_graph"].nnz, sha(A.uns["stoch_graph"].data)))
if "stage" in A.obs:
    # terminal_states runs ARPACK eigs(k=10, which="LR") at tol=0 on the velocity transition matrix:
    # 32-40 s on Graviton4 and Intel, and still iterating after 20 min on AMD's graph (a path).
    probe("probe_velocity_pseudotime", lambda: scv.tl.velocity_pseudotime(A, vkey="stoch"), limit=300)
    if "stoch_pseudotime" in A.obs:
        st = A.obs["stage"].astype(str).str.replace("E", "").replace("mixed_gastrulation", "nan")
        st = pd.to_numeric(st, errors="coerce").to_numpy()
        m = np.isfinite(st)
        np.save(os.path.join(OUT, "pseudotime.npy"), A.obs["stoch_pseudotime"].to_numpy("float64"))
        rec("pseudotime_vs_stage", "Spearman %.4f over %d staged cells (reported, not asserted)"
            % (spearmanr(A.obs["stoch_pseudotime"].to_numpy()[m], st[m])[0], int(m.sum())))

rec("peak_rss_gib", "%.2f" % maxrss())
rec("wall_s", "%.1f" % (time.time() - T0))
dump()
print("SCVELO REAL OK", flush=True)
