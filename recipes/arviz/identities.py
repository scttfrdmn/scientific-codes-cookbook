#!/usr/bin/env python3
"""ArviZ diagnostics on Graviton, against an exact invariance and a closed form.

The cmdstan recipe in this env already asserts R-hat, MCSE-bounded posterior means, zero
divergences and seed reproducibility. This one does not restate any of that. It checks the
DIAGNOSTIC ESTIMATORS themselves, which needs no sampler at all -- the draws are synthesised with
known autocorrelation, so the right answers are derivable:

  AN EXACT INVARIANCE. Rank-normalised split-R-hat is computed from the RANKS of the draws, so it
  must be bit-identical under any strictly monotone transformation. Measured 0.000e+00 under an
  affine map, exp, and a cube. An implementation that normalised wrongly could not satisfy this.

  A CLOSED FORM FOR THE ESTIMATOR. An AR(1) chain with lag-1 correlation rho has integrated
  autocorrelation time (1+rho)/(1-rho), so ESS/N -> (1-rho)/(1+rho). That is a theoretical
  reference for ESS itself rather than a band on an observed number.

  A CONTROL. Chains drawn from DIFFERENT distributions must give R-hat far above 1, or the
  invariance above would be a property of an estimator that cannot detect non-convergence.

ONE THING NOT ASSERTED, because the probe showed it is false: "ESS <= total draws" is NOT a
structural bound. Negatively autocorrelated (antithetic) draws legitimately give ESS > N --
measured 1.0060 on i.i.d. draws, and it is routine for NUTS. It is reported instead.

Run with `python3 -u`.
"""
import sys
import warnings

import numpy as np

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def dump():
    with open("/tmp/score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    dump()
    sys.exit("FAIL: %s" % msg)


with warnings.catch_warnings():
    # arviz 1.x emits a MigrationWarning for InferenceData on import; the package is fine.
    warnings.simplefilter("ignore")
    import arviz as az

rec("arviz", az.__version__)
rec("numpy", np.__version__)
rec("arviz_constructor", "az.convert_to_dataset")

SEED = 20261011
NCHAIN, NDRAW = 4, 8000
NTOT = NCHAIN * NDRAW
INVARIANCE_TOL = 0.0      # rank-based, so exact equality is the claim
AR1_TOL = 0.30            # see the justification where it is used
MCSE_TOL = 1e-3           # likewise
rng = np.random.default_rng(SEED)


def idata(arr, name="theta"):
    """Wrap a (chain, draw) array for arviz 1.x.

    `az.convert_to_dataset({"theta": arr})` is the call that works; `az.from_dict` wants a dict
    of GROUPS (`{"posterior": {...}}`) and raises
    AttributeError: 'numpy.ndarray' object has no attribute 'items' on a flat dict.

    This cost a run because the probe that discovered the API tried both inside a try/except and
    reported only that *something* worked -- so the one fact needed was thrown away, and the
    recipe was written against the failing call. A probe whose job is to find an API has to
    report WHICH call succeeded.
    """
    return az.convert_to_dataset({name: arr})


def ar1(rho, n=NDRAW, c=NCHAIN):
    """AR(1) with lag-1 correlation rho and unit marginal variance."""
    eps = rng.normal(scale=np.sqrt(1 - rho ** 2), size=(c, n))
    x = np.empty((c, n))
    x[:, 0] = rng.normal(size=c)
    for t in range(1, n):
        x[:, t] = rho * x[:, t - 1] + eps[:, t]
    return x


def rhat(a):
    return float(np.asarray(az.rhat(idata(a))["theta"]))


def ess(a):
    return float(np.asarray(az.ess(idata(a))["theta"]))


# ---- 1. the exact invariance -------------------------------------------------------------
base = ar1(0.5)
r0 = rhat(base)
rec("rhat_base", "%.17f" % r0)
worst = 0.0
for label, f in (("affine 3.7x+112.5", lambda v: 3.7 * v + 112.5),
                 ("exp", np.exp),
                 ("cube", lambda v: v ** 3)):
    r = rhat(f(base))
    d = abs(r - r0)
    worst = max(worst, d)
    rec("rhat_under_%s" % label.split()[0], "%.17f  (diff %.3e)" % (r, d))
    if d != INVARIANCE_TOL:
        die("R-hat moved by %.3e under a %s transformation; rank normalisation should make it "
            "bit-identical" % (d, label))
rec("rhat_invariance_worst_diff", "%.3e" % worst)
rec("identity_rank_invariance",
    "R-hat is bit-identical (0.0) under an affine map, exp and a cube -- it is computed from "
    "ranks, so only the ORDER of the draws can change it")

# ---- 2. the control: R-hat must detect chains that disagree ------------------------------
# Without this, the invariance above could hold for an estimator that returns a constant.
bad = np.vstack([rng.normal(0.0, 1.0, size=(NCHAIN // 2, NDRAW)),
                 rng.normal(5.0, 1.0, size=(NCHAIN // 2, NDRAW))])
r_bad = rhat(bad)
rec("rhat_chains_from_different_distributions", "%.6f" % r_bad)
rec("rhat_wellmixed_for_contrast", "%.6f" % r0)
if r_bad <= 1.5:
    die("badly-mixed chains gave R-hat = %.6f; the estimator is not discriminating" % r_bad)
rec("identity_rhat_discriminates",
    "chains drawn from N(0,1) and N(5,1) give R-hat = %.3f against %.5f for well-mixed ones, "
    "so the invariance above is a property of a working estimator" % (r_bad, r0))

# ---- 3. ESS against the AR(1) closed form ------------------------------------------------
# The band is 30%, which is loose -- and justified by what it must catch rather than by how
# close the run landed. An estimator that ignored autocorrelation entirely would report
# ESS/N ~ 1 at every rho, i.e. a ratio of 9.0 at rho=0.8. So 1.30 is about 30x tighter than the
# error it exists to detect, while tolerating the finite-sample bias of the estimator itself
# (worst observed 0.876 at rho=0.8 with N=32000).
rows = []
for rho in (-0.5, 0.0, 0.5, 0.8, 0.9):
    frac = ess(ar1(rho)) / NTOT
    theory = (1 - rho) / (1 + rho)
    rows.append((rho, frac, theory, frac / theory))
    rec("ess_fraction_rho_%.1f" % rho,
        "%.4f measured, %.4f closed form, ratio %.3f" % (frac, theory, frac / theory))
worst_ar = max(abs(r - 1.0) for _, _, _, r in rows)
rec("ar1_worst_relative_departure", "%.3f" % worst_ar)
if worst_ar > AR1_TOL:
    die("ESS departs from the AR(1) closed form by %.1f%%, over the %.0f%% bound"
        % (100 * worst_ar, 100 * AR1_TOL))
naive = 1.0 / ((1 - 0.8) / (1 + 0.8))
rec("ar1_discrimination",
    "an estimator ignoring autocorrelation would report ratio %.1f at rho=0.8" % naive)
# The rho list is derived from the data rather than written out, so the claim cannot drift from
# the rungs actually run -- an earlier version said four when five were measured.
rec("identity_ess_closed_form",
    "ESS tracks (1-rho)/(1+rho) across rho = %s to within %.1f%%, against a %.0f%% bound set by "
    "what it must catch (a %.0fx error)"
    % (", ".join("%g" % r[0] for r in rows), 100 * worst_ar, 100 * AR1_TOL, naive))

# ---- 4. the algebraic relation among arviz's own outputs ---------------------------------
# mcse_mean should be sd/sqrt(ess_mean). It holds to ~1e-4 rather than exactly, because arviz's
# internal variance estimate is not the plain pooled sample sd -- so this is asserted as a
# near-identity with the residual reported, not as an equality.
e = ess(base)
m = float(np.asarray(az.mcse(idata(base))["theta"]))
sd = float(base.std(ddof=1))
pred = sd / np.sqrt(e)
rel = abs(m - pred) / pred
rec("mcse_reported", "%.8f" % m)
rec("mcse_from_sd_over_sqrt_ess", "%.8f" % pred)
rec("mcse_relative_difference", "%.3e" % rel)
if rel > MCSE_TOL:
    die("mcse departs from sd/sqrt(ess) by %.3e, over %.0e" % (rel, MCSE_TOL))
rec("identity_mcse_relation",
    "mcse agrees with sd/sqrt(ess) to %.1e -- a relation among three separately computed "
    "outputs, holding to four decimals rather than exactly because arviz's internal variance "
    "estimate is not the pooled sample sd" % rel)

# ---- 5. reported, not asserted: ESS can exceed N -----------------------------------------
# "ESS <= total draws" looks like a structural bound and is not one. Rather than assert the
# negation from a fixture that happens to show it, the rho = -0.5 rung above DEMONSTRATES it:
# an antithetic chain has closed-form ESS/N = (1-rho)/(1+rho) = 3.0, i.e. three times the number
# of draws. The same thing happens for real with NUTS, which is often antithetic.
anti = [r for r in rows if r[0] == -0.5][0]
rec("ess_fraction_antithetic_rho_-0.5", "%.4f measured, %.4f closed form" % (anti[1], anti[2]))
if anti[1] <= 1.0:
    die("the antithetic rung gave ESS/N = %.4f; it should exceed 1 and so demonstrate that "
        "'ESS <= N' is not a bound" % anti[1])
rec("identity_ess_exceeds_n",
    "an antithetic chain (rho = -0.5) gives ESS/N = %.3f against a closed form of %.1f -- so "
    "'ESS <= total draws' is NOT a structural bound, and asserting it would be a false "
    "invariant. Demonstrated rather than claimed." % (anti[1], anti[2]))

dump()
print("ARVIZ OK")
