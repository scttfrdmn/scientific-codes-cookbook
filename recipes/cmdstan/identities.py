#!/usr/bin/env python3
"""CmdStan checks: three exact analytical references, then the sampler against the closed form.

Staged as a pinned input rather than inlined in the TaskSpec because a spawn task command
travels in EC2 user data, capped at 16,384 bytes. Run with `python3 -u` so a killed task still
has this output in command.log.
"""
import json
import math
import os
import sys
import time

import numpy as np
import cmdstanpy

out = {}


def rec(k, v):
    out[k] = v
    print("%s\t%s" % (k, v))


def fail(msg):
    sys.exit("FAIL: %s" % msg)


rec("cmdstanpy_version", getattr(cmdstanpy, "__version__", "unknown"))
rec("cmdstan_version", ".".join(str(x) for x in cmdstanpy.cmdstan_version()))

D = json.load(open("data.json"))
N, Y, A0, B0 = int(D["N"]), int(D["y"]), float(D["a"]), float(D["b"])
rec("data", "N=%d y=%d prior Beta(%g,%g)" % (N, Y, A0, B0))

# ---------------------------------------------------------------------------------------------
# THE CLOSED FORM. Conjugacy gives the posterior exactly: Beta(a+y, b+N-y).
#
# Two exponent pairs matter below, and the difference between them is the whole reason this
# model was chosen:
#   * on the CONSTRAINED scale (jacobian=False) the kernel is (a-1+y)logT + (b-1+N-y)log(1-T)
#   * on the UNCONSTRAINED scale (jacobian=True) Stan adds log T + log(1-T) for the logit
#     transform, which turns the kernel into exactly (a+y)logT + (b+N-y)log(1-T) -- the
#     posterior Beta kernel itself.
# So checking both settings tests the Jacobian adjustment, which is the part that decides
# whether the sampler targets the right distribution at all.
# ---------------------------------------------------------------------------------------------
APOST, BPOST = A0 + Y, B0 + N - Y                     # Beta(19, 36) for the shipped data
CF_A, CF_B = A0 - 1 + Y, B0 - 1 + N - Y               # constrained-scale exponents
EXACT_MEAN = APOST / (APOST + BPOST)
EXACT_VAR = APOST * BPOST / ((APOST + BPOST) ** 2 * (APOST + BPOST + 1))
EXACT_SD = math.sqrt(EXACT_VAR)
MODE_CONSTRAINED = (APOST - 1) / (APOST + BPOST - 2)  # mode of the Beta density in theta
rec("posterior_exact", "Beta(%g,%g)" % (APOST, BPOST))
rec("exact_mean", "%.15f" % EXACT_MEAN)
rec("exact_sd", "%.15f" % EXACT_SD)
rec("exact_mode_constrained", "%.15f" % MODE_CONSTRAINED)

t0 = time.time()
model = cmdstanpy.CmdStanModel(stan_file="beta_binomial.stan")
rec("compile_seconds", "%.1f" % (time.time() - t0))

# =============================================================================================
# IDENTITY 1 -- the gradient is exactly linear in theta, and needs no convention at all.
#
# cmdstanpy reports the gradient with respect to the UNCONSTRAINED parameter z = logit(theta),
# so d/dz = (dlp/dtheta) * theta(1-theta), and the log-ratio kernel collapses to a straight
# line:
#     jacobian=False :  grad_z = (a-1+y)(1-T) - (b-1+N-y)T
#     jacobian=True  :  grad_z = (a+y)(1-T)   - (b+N-y)T
# This is the strongest check available here because **additive constants vanish under
# differentiation** -- it cannot be affected by which normalising terms Stan chooses to drop.
# =============================================================================================
THETAS = (0.2, 0.34, 0.5, 0.7)


def log_prob(theta, jacobian):
    df = model.log_prob({"theta": theta}, data="data.json", jacobian=jacobian)
    row = df.iloc[0]
    gcols = [c for c in df.columns if c.startswith("g_")]
    if not gcols:
        fail("log_prob returned no gradient column; columns were %s" % list(df.columns))
    return float(row["lp__"]), float(row[gcols[0]])


worst_grad = 0.0
for jac, (ca, cb) in ((False, (CF_A, CF_B)), (True, (APOST, BPOST))):
    for th in THETAS:
        lp, g = log_prob(th, jac)
        analytic = ca * (1.0 - th) - cb * th
        d = abs(g - analytic)
        worst_grad = max(worst_grad, d)
        print("   grad jacobian=%-5s theta=%.2f  stan=%+.10f  analytic=%+.10f  diff=%.2e"
              % (jac, th, g, analytic, d))
rec("max_gradient_difference", "%.3e" % worst_grad)
# Double-precision evaluation of a handful of logs and a division; nothing iterative.
if worst_grad > 1e-8:
    fail("gradient differs from the analytical value by %.3e" % worst_grad)
rec("identity_gradient",
    "grad_z is exactly (a-1+y)(1-T)-(b-1+N-y)T and (a+y)(1-T)-(b+N-y)T")

# =============================================================================================
# IDENTITY 2 -- log-density DIFFERENCES match the analytical kernel.
#
# Differences are used rather than absolute values on purpose: Stan's `~` statements drop terms
# constant in theta, so an absolute comparison would also be asserting that convention. A
# difference cancels any additive constant, so it tests the density and nothing else. The
# absolute agreement is REPORTED below, because it is informative but convention-dependent.
# =============================================================================================
worst_lp = 0.0
for jac, (ca, cb) in ((False, (CF_A, CF_B)), (True, (APOST, BPOST))):
    base_lp, _ = log_prob(THETAS[0], jac)
    base_an = ca * math.log(THETAS[0]) + cb * math.log(1.0 - THETAS[0])
    for th in THETAS[1:]:
        lp, _ = log_prob(th, jac)
        an = ca * math.log(th) + cb * math.log(1.0 - th)
        d = abs((lp - base_lp) - (an - base_an))
        worst_lp = max(worst_lp, d)
        print("   dlp  jacobian=%-5s theta=%.2f  stan=%+.10f  analytic=%+.10f  diff=%.2e"
              % (jac, th, lp - base_lp, an - base_an, d))
rec("max_logdensity_difference", "%.3e" % worst_lp)
if worst_lp > 1e-6:
    fail("log-density differences deviate by %.3e" % worst_lp)
rec("identity_logdensity", "log-density differences match the analytical kernel")

# Reported, not asserted: with `~` statements Stan drops both normalising constants, so lp__
# comes out as the BARE kernel. True of this version; a convention, not a law.
lp0, _ = log_prob(THETAS[0], False)
bare = CF_A * math.log(THETAS[0]) + CF_B * math.log(1.0 - THETAS[0])
rec("absolute_lp_minus_bare_kernel", "%.3e" % abs(lp0 - bare))

# =============================================================================================
# IDENTITY 3 -- the mode, where the gradient identity has a known root.
#
# grad_z(jacobian=True) = (a+y)(1-T) - (b+N-y)T vanishes at T = (a+y)/(a+b+N), which is the
# posterior MEAN. So optimising on the unconstrained scale must land on the mean, while
# optimising on the constrained scale lands on the Beta mode. Two different correct answers from
# one model -- and a check that the Jacobian flag does what it says.
# =============================================================================================
def optimise(jacobian):
    try:
        f = model.optimize(data="data.json", jacobian=jacobian, seed=4321, show_console=False)
        return float(np.atleast_1d(f.stan_variable("theta"))[0]), "jacobian=%s" % jacobian
    except TypeError:
        # Older cmdstanpy has no `jacobian` kwarg; its default is the constrained scale.
        if jacobian:
            return None, "unsupported"
        f = model.optimize(data="data.json", seed=4321, show_console=False)
        return float(np.atleast_1d(f.stan_variable("theta"))[0]), "default (constrained)"


map_c, how_c = optimise(False)
rec("map_constrained", "%.10f (%s)" % (map_c, how_c))
if abs(map_c - MODE_CONSTRAINED) > 1e-6:
    fail("constrained-scale MAP %.10f != Beta mode %.10f" % (map_c, MODE_CONSTRAINED))
rec("identity_map_mode", "constrained-scale MAP == the Beta posterior mode")

map_u, how_u = optimise(True)
if map_u is None:
    rec("map_unconstrained", "skipped (%s)" % how_u)
else:
    rec("map_unconstrained", "%.10f (%s)" % (map_u, how_u))
    if abs(map_u - EXACT_MEAN) > 1e-6:
        fail("unconstrained-scale MAP %.10f != posterior mean %.10f" % (map_u, EXACT_MEAN))
    rec("identity_map_mean", "unconstrained-scale MAP == the posterior MEAN (a+y)/(a+b+N)")

# =============================================================================================
# THE SAMPLER -- against the closed form, with the tolerance taken from its own reported MCSE.
#
# A fixed seed makes this deterministic, so it cannot go flaky; but the tolerance is still
# stated as a multiple of the Monte Carlo standard error Stan itself reports, because that is
# the estimator's own uncertainty rather than a number picked to pass.
# =============================================================================================
SEED, CHAINS = 20260101, 4
fit = model.sample(data="data.json", chains=CHAINS, parallel_chains=1,
                   iter_warmup=1000, iter_sampling=4000, seed=SEED, show_progress=False)
summ = fit.summary()
row = summ.loc["theta"]
mean, mcse, sd, rhat = (float(row["Mean"]), float(row["MCSE"]),
                        float(row["StdDev"]), float(row["R_hat"]))
rec("draws_shape", "x".join(str(d) for d in np.asarray(fit.draws()).shape))
rec("sampled_mean", "%.10f" % mean)
rec("sampled_mcse", "%.10f" % mcse)
rec("sampled_sd", "%.10f" % sd)
rec("r_hat", "%.6f" % rhat)

if mcse <= 0:
    fail("reported MCSE is %g -- the tolerance would be meaningless" % mcse)
z = abs(mean - EXACT_MEAN) / mcse
rec("mean_error_in_mcse_units", "%.3f" % z)
# Four standard errors: a two-sided event of probability ~6e-5 if the sampler is correct.
if z > 4.0:
    fail("posterior mean is %.2f MCSE from the exact value %.15f" % (z, EXACT_MEAN))
rec("identity_mean", "sampled mean is within %.2f MCSE of the exact %.10f" % (z, EXACT_MEAN))

sd_rel = abs(sd - EXACT_SD) / EXACT_SD
rec("sd_relative_error", "%.4f" % sd_rel)
# The sample sd of M effectively independent draws has relative error ~1/sqrt(2*ESS); with
# thousands of draws, 5% is loose and still far tighter than any wrong posterior would give.
if sd_rel > 0.05:
    fail("sampled sd %.6f is %.1f%% from the exact %.6f" % (sd, 100 * sd_rel, EXACT_SD))
rec("identity_sd", "sampled sd is within %.2f%% of the exact %.10f" % (100 * sd_rel, EXACT_SD))

if abs(rhat - 1.0) > 0.01:
    fail("R-hat is %.6f -- the chains have not mixed" % rhat)
ndiv = int(np.sum(np.asarray(fit.method_variables()["divergent__"])))
rec("divergent_transitions", ndiv)
if ndiv != 0:
    fail("%d divergent transitions" % ndiv)

diag = fit.diagnose()
rec("diagnose_clean", "yes" if "no problems detected" in diag else "NO")
if "no problems detected" not in diag:
    print(diag)
    fail("CmdStan's own diagnostics reported a problem")

# =============================================================================================
# A seeded sampler must be reproducible before any exact claim about it means anything.
# =============================================================================================
fit2 = model.sample(data="data.json", chains=CHAINS, parallel_chains=1,
                    iter_warmup=1000, iter_sampling=4000, seed=SEED, show_progress=False)
same = np.array_equal(np.asarray(fit.draws()), np.asarray(fit2.draws()))
rec("same_seed_identical", "yes" if same else "NO")
if not same:
    fail("two runs at seed %d differ -- nothing about the draws is reproducible" % SEED)

fit3 = model.sample(data="data.json", chains=CHAINS, parallel_chains=1,
                    iter_warmup=1000, iter_sampling=4000, seed=SEED + 1, show_progress=False)
differs = not np.array_equal(np.asarray(fit.draws()), np.asarray(fit3.draws()))
# Reported, not asserted: if a different seed happened to coincide that would be a fact about
# this model, not a failure.
rec("different_seed_differs", "yes" if differs else "no")
rec("identity_seed", "two runs at seed %d are bit-identical" % SEED)

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in out.items():
        fh.write("%s\t%s\n" % (k, v))
print("CMDSTAN OK")
