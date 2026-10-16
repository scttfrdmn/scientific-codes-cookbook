#!/usr/bin/env python3
"""scVelo on Graviton4: RNA velocity on counts simulated from the kinetic model with known rates.

du/dt = alpha - beta*u, ds/dt = beta*u - gamma*s, with a per-gene switch from induction
(alpha > 0) to repression (alpha = 0). scVelo's own simulator draws the counts; every truth the
checks use is recomputed here independently, so nothing is fetched and nothing is circular:

 1. The simulator. Its noise-free output equals the closed-form solution of the ODEs, written
    out below, at cell times reconstructed from the simulator's documented draw.
 2. Moments. Ms/Mu are the mean over each cell and its graph neighbours.
 3. The steady-state model. gamma is least squares of Mu on Ms through the origin over the
    cells in the upper 5% of Ms/max + Mu/max, recomputed in numpy; velocity is Mu - gamma*Ms
    exactly.
 4. Recovery, stated against chance rather than as bands: the steady-state gamma and the
    dynamical model's gamma/beta rank the planted gamma/beta, each induction/repression call
    matches the planted phase, and velocity signs match the true ds/dt -- each beyond 5 sigma of
    its null.

The dynamical model needs two workarounds on this env's pandas 3 (read-only `.values`); each
is probed first and recorded, so a fixed digest shows up as "works".

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import random
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


def probe(label, fn):
    """Run a call expected to fail on this env; record how, never let it end the run."""
    try:
        fn()
        rec(label, "works")
        return None
    except Exception as exc:
        rec(label, "raises %s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:100]))
        return exc


warnings.simplefilter("ignore")
import pandas as pd
import scanpy as sc
import scvelo as scv
from scipy.stats import spearmanr

scv.settings.verbosity = 0
ver("scvelo", lambda: scv.__version__)
ver("scanpy", lambda: sc.__version__)
ver("pandas", lambda: pd.__version__)
ver("numpy", lambda: np.__version__)

SEED = 20261016
rng = np.random.default_rng(SEED)
G, N, TMAX, SIM_SEED = 100, 1000, 25.0, 0
# Rates spread over a decade each, so the ranks being recovered are not a near-tie.
beta = np.exp(rng.uniform(np.log(0.3), np.log(3.0), G))
gamma = np.exp(rng.uniform(np.log(0.1), np.log(1.0), G))
alpha = np.exp(rng.uniform(np.log(2.0), np.log(10.0), G))
switch = rng.uniform(0.3, 0.8, G)          # fraction of t_max at which each gene switches off
kw = dict(n_obs=N, n_vars=G, t_max=TMAX, alpha=list(alpha), beta=list(beta), gamma=list(gamma),
          switches=list(switch), random_seed=SIM_SEED)
A0 = scv.datasets.simulation(noise_level=0, **kw)
A = scv.datasets.simulation(noise_level=1, **kw)
rec("simulated", "%d cells x %d genes, noise_level 1 (and a noise-free twin)" % (N, G))

# =================================================== 1. the simulator == the closed form
# Cell times: scv.datasets.simulation draws exponential gaps from Python's `random` seeded with
# random_seed, then rescales to t_max. Reconstructed here, checked against true_t (rounded to 2
# dp by the simulator) and true_t_ (exact).
random.seed(SIM_SEED)
t = np.insert(np.cumsum([-0.1 * np.log(random.uniform(0, 1)) for _ in range(N - 1)]), 0, 0.0)
t *= TMAX / np.max(t)
tsw = np.array([np.max(t[t < f * TMAX]) for f in switch])
dt_ = float(np.max(np.abs(np.round(t, 2) - A.obs["true_t"].to_numpy())))
dts = float(np.max(np.abs(tsw - A.var["true_t_"].to_numpy())))
rec("cell_times", "reconstructed vs true_t %.1e, switch times vs true_t_ %.1e" % (dt_, dts))
if dt_ != 0.0 or dts != 0.0:
    die("could not reconstruct the simulator's cell times (%.3e / %.3e)" % (dt_, dts))


def closed(tt, a, b, g, ts):
    """Analytic solution from u = s = 0, alpha -> 0 at ts. Requires b != g (true here: continuous draws)."""
    def u_(tau, u0, a):
        return u0 * np.exp(-b * tau) + a / b * (1 - np.exp(-b * tau))

    def s_(tau, s0, u0, a):
        return (s0 * np.exp(-g * tau) + a / g * (1 - np.exp(-g * tau))
                + (a - b * u0) / (g - b) * (np.exp(-g * tau) - np.exp(-b * tau)))
    u1, s1 = u_(ts, 0.0, a), s_(ts, 0.0, 0.0, a)
    on = tt < ts
    return (np.where(on, u_(tt, 0.0, a), u_(tt - ts, u1, 0.0)),
            np.where(on, s_(tt, 0.0, 0.0, a), s_(tt - ts, s1, u1, 0.0)))


U = np.zeros((N, G))
S = np.zeros((N, G))
for i in range(G):
    U[:, i], S[:, i] = closed(t, alpha[i], beta[i], gamma[i], tsw[i])
du = float(np.max(np.abs(U - A0.layers["unspliced"]) / U.max(0)))
ds = float(np.max(np.abs(S - A0.layers["spliced"]) / S.max(0)))
finite("closed-form solution", U, S)
rec("simulator_vs_closed_form", "noise-free u rel %.2e, s rel %.2e" % (du, ds))
# The same expression evaluated two ways in float64: round-off only, so 1e-12.
if du > 1e-12 or ds > 1e-12:
    die("the simulator does not solve du/dt = a - b u, ds/dt = b u - g s (rel %.3e / %.3e)" % (du, ds))
on_true = t[:, None] < tsw[None, :]                      # planted phase: induction
V_true = beta * U - gamma * S                            # true ds/dt

# ============================================= 2. moments == neighbourhood means (float32)
sc.pp.pca(A, n_comps=30, random_state=0)
sc.pp.neighbors(A, n_neighbors=30, random_state=0)
scv.pp.moments(A, n_neighbors=30)
Cn = (A.obsp["connectivities"] > 0).tolil()
Cn.setdiag(True)
Cn = Cn.tocsr().astype("float64")
k = np.asarray(Cn.sum(1)).ravel()
Ms = np.asarray(A.layers["Ms"], dtype="float64")
Mu = np.asarray(A.layers["Mu"], dtype="float64")
dMs = float(np.max(np.abs((Cn @ A.layers["spliced"]) / k[:, None] - Ms) / Ms.max(0)))
dMu = float(np.max(np.abs((Cn @ A.layers["unspliced"]) / k[:, None] - Mu) / Mu.max(0)))
rec("moments", "Ms rel %.2e, Mu rel %.2e; %d-%d cells per neighbourhood" % (dMs, dMu, int(k.min()), int(k.max())))
# scVelo stores moments as float32 (eps 1.2e-7); 1e-6 is that cast plus summation headroom.
if dMs > 1e-6 or dMu > 1e-6:
    die("moments are not neighbourhood means (rel %.3e / %.3e)" % (dMs, dMu))

# ======================================= 3. steady-state model: gamma and velocity, exactly
scv.tl.velocity(A, mode="deterministic")
g_sc = A.var["velocity_gamma"].to_numpy(dtype="float64")
nrm = Ms / np.clip(Ms.max(0), 1e-3, None) + Mu / np.clip(Mu.max(0), 1e-3, None)
sel = nrm >= np.percentile(nrm, 95, axis=0)             # default perc=[5, 95], no offset -> upper 5%
g_np = (Ms * Mu * sel).sum(0) / (Ms * Ms * sel).sum(0)
finite("steady-state gamma", g_sc, g_np)
dg = float(np.max(np.abs(g_sc - g_np) / g_np))
rec("ss_gamma_vs_numpy", "max rel %.2e over %d genes" % (dg, G))
# scVelo regresses in float32; 1e-5 is float32 eps x the ~50 selected cells, with headroom.
if dg > 1e-5:
    die("steady-state gamma is not the extreme-quantile least-squares slope (rel %.3e)" % dg)
V_ss = A.layers["velocity"]
dv = float(np.max(np.abs(V_ss - (A.layers["Mu"] - A.var["velocity_gamma"].to_numpy() * A.layers["Ms"]))))
rec("ss_velocity_identity", "max |velocity - (Mu - gamma Ms)| %.3e" % dv)
if dv != 0.0:
    die("steady-state velocity is not Mu - gamma*Ms (%.3e)" % dv)

# ============================================================== 4. recovery, against chance
# Spearman under the null of no recovery has sd 1/sqrt(G-1); 5 sigma is 0.50 at G = 100.
rho_bound = 5.0 / np.sqrt(G - 1)
ratio = gamma / beta
rho_ss = float(spearmanr(g_sc, ratio)[0])
rec("ss_gamma_rank", "Spearman vs planted gamma/beta %.4f (5-sigma null bound %.3f)" % (rho_ss, rho_bound))
if not rho_ss > rho_bound:
    die("steady-state gamma does not rank the planted gamma/beta (%.4f)" % rho_ss)

# The dynamical model on this env. Probe the documented call first, then work around.
import scvelo.tools._em_model_core as emc

err = probe("probe_recover_dynamics_default",
            lambda: scv.tl.recover_dynamics(A.copy(), var_names="all", n_jobs=1, show_progress_bar=False))
if isinstance(err, TypeError):
    # make_unique_list passes a list to pandas.unique, which pandas 3 rejects. The same
    # order-preserving de-duplication, without pandas:
    _orig = emc.make_unique_list

    def _unique_list(key, allow_array=False):
        if isinstance(key, (list, tuple)) and all(isinstance(x, str) for x in key):
            return list(dict.fromkeys(key))
        return _orig(key, allow_array=allow_array)
    emc.make_unique_list = _unique_list
    rec("workaround_unique", "make_unique_list -> dict.fromkeys (pandas.unique rejects a list)")
    probe("probe_recover_dynamics_aligned",
          lambda: scv.tl.recover_dynamics(A.copy(), var_names="all", n_jobs=1, show_progress_bar=False))
# t_max=False skips align_dynamics, which writes into read-only .values. Alignment rescales a
# gene's alpha, beta, gamma and time by ONE factor, so gamma/beta and the phase calls are unchanged.
scv.tl.recover_dynamics(A, var_names="all", t_max=False, n_jobs=1, show_progress_bar=False)
fb = A.var["fit_beta"].to_numpy(dtype="float64")
fg = A.var["fit_gamma"].to_numpy(dtype="float64")
finite("dynamical-model rates", fb, fg)
rho_dyn = float(spearmanr(fg / fb, ratio)[0])
rec("dyn_gamma_over_beta_rank", "Spearman vs planted gamma/beta %.4f (bound %.3f)" % (rho_dyn, rho_bound))
if not rho_dyn > rho_bound:
    die("dynamical gamma/beta does not rank the planted ratio (%.4f)" % rho_dyn)

# Phase: the model calls a cell 'induction' when its latent time precedes the gene's switch.
T = np.asarray(A.layers["fit_t"], dtype="float64")
ts_fit = A.var["fit_t_"].to_numpy(dtype="float64")
finite("latent times", T, ts_fit)
on_fit = T < ts_fit[None, :]
n_cells = on_true.size
p0 = max(on_true.mean(), 1 - on_true.mean())            # best constant guess
acc_bound = p0 + 5 * np.sqrt(p0 * (1 - p0) / n_cells)
acc = float((on_fit == on_true).mean())
rec("phase_calls", "%.4f of %d cell-gene calls match the planted phase (constant guess %.4f, 5-sigma %.4f)"
    % (acc, n_cells, p0, acc_bound))
if not acc > acc_bound:
    die("induction/repression calls are no better than a constant guess (%.4f)" % acc)

probe("probe_velocity_dynamical", lambda: scv.tl.velocity(A.copy(), mode="dynamical"))
scv.tl.velocity(A, mode="dynamical_residuals", vkey="dyn_velocity")
V_dyn = np.asarray(A.layers["dyn_velocity"], dtype="float64")
finite("dynamical velocity", V_dyn)
nz = V_true != 0
sign_bound = 0.5 + 5 * 0.5 / np.sqrt(nz.sum())
a_ss = float((np.sign(V_ss) == np.sign(V_true))[nz].mean())
a_dyn = float((np.sign(V_dyn) == np.sign(V_true))[nz].mean())
rec("velocity_sign", "matches true ds/dt in %.4f (steady-state) / %.4f (dynamical) of %d (5-sigma %.4f)"
    % (a_ss, a_dyn, int(nz.sum()), sign_bound))
if not (a_ss > sign_bound and a_dyn > sign_bound):
    die("velocity signs no better than chance: %.4f / %.4f" % (a_ss, a_dyn))

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k_, v in ROWS:
        fh.write("%s\t%s\n" % (k_, v))

print("SCVELO IDENTITIES OK")
