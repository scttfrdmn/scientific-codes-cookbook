---
tool: cmdstan
tool_version: "2.40"
env: bayes
image: quay.io/aarchsci/bayes@sha256:cfa7cc144c6118155b4c3da60a5623a9eaa71db80c22fdbf67f811949c92a2fb
spawn_version: 0.126.1
last_verified: 2026-10-09
---
# CmdStan — Bayesian inference on Graviton, against a closed-form posterior

Compiles and samples a conjugate Beta-Binomial model on Graviton4, checking Stan's log density, gradients, MAP and posterior against the exact analytical answer. For anyone running Stan on ARM.

## Run it

```bash
make stage RECIPE=cmdstan      # once: the model, four integers of data, and the checks
spawn task run --spec "$(make -s spec RECIPE=cmdstan)" --wait
make ls RECIPE=cmdstan

model = cmdstanpy.CmdStanModel(stan_file="beta_binomial.stan")   # compiles, ~10 s
model.log_prob({"theta": 0.2}, data="data.json", jacobian=False) # lp__ and the gradient
model.optimize(data="data.json", jacobian=True, seed=4321)       # MAP
model.sample(data="data.json", chains=4, iter_warmup=1000, iter_sampling=4000, seed=20260101)
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the Beta-Binomial | your model | **this one is here because its posterior is known exactly**: Beta prior + binomial likelihood gives `Beta(a+y, b+N-y)`. Swap it and you lose the analytical reference, not just the numbers. |
| `jacobian=True/False` | — | **check both.** `True` adds the logit transform's `log θ + log(1−θ)`, which turns the constrained kernel into the posterior Beta kernel itself. It is the flag that decides whether the sampler targets the right distribution. |
| `seed=20260101` | any fixed value | **keep it fixed.** HMC is stochastic; a seeded run is bit-reproducible and an unseeded one makes every exact claim below flaky ([same rule the assemblers earned](../../practices/cross-checks.md)). |
| `model.log_prob(...)` | — | **the part worth copying.** It evaluates the density with no sampling at all, so you can check a model against hand arithmetic before spending a single MCMC draw. |
| `chains=4, iter_sampling=4000` | your own | sampling is effectively instant here; the 10 s **compile** dominates. Budget for compilation, not iterations. |
| `stan_variable("theta")` | — | wrap in `np.atleast_1d`: cmdstanpy warns this will always return an array in a future release. |

**Leave the fixture.** Four integers (`N=50, y=17`, prior `Beta(2,3)`) are the whole dataset, and that is the point — the posterior is `Beta(19,36)` on paper, so every check is a comparison against arithmetic. **Scale it** to your own model once it passes; `log_prob` and the seed discipline transfer unchanged.

## Shape, size, cost

One task on `m8g.xlarge` (4 vCPU / 16 GiB), TTL 40m as a **backstop** with `cost_limit` $0.15 as the real guard. **Compiling the model takes 10.0 s and dominates**; three full 4-chain fits are seconds. The recorded **85 s** is mostly boot, Docker install and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a gradient exact to 1.35e-15, two MAPs with different known answers, and a sampler checked against its own stated error</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pins, count asserted | 3 of 3 |
| toolchain | `g++` and `make` present before anything compiles | both |
| cmdstan / cmdstanpy | read from the running install | **2.40** / 1.3.0 |
| **gradient, both Jacobian settings** | **max diff < 1e-8 at 4 θ values each** | **1.350e-15** (5 of 8 exactly 0) |
| **log-density differences** | **max diff < 1e-6** | **7.235e-07** |
| absolute `lp__` vs the bare kernel | *reported, not asserted* | 2.802e-07 |
| **constrained MAP** | **== Beta mode 0.339622641509434** | **0.3396226500** |
| **unconstrained MAP** | **== posterior mean 0.345454545454545** | **0.3454545700** |
| **sampled mean** | **within 4 MCSE of the exact mean** | **0.125 MCSE** |
| sampled sd | within 5% of the exact 0.063543531546624 | 0.58% |
| R-hat | \|R̂ − 1\| < 0.01 | 1.000420 |
| divergent transitions | == 0 | **0** |
| CmdStan's own diagnostics | "no problems detected" | yes |
| **same seed** | **bit-identical draws** | **yes** (4000×4×8) |
| different seed differs | *reported, not asserted* | yes |

### The strongest check is the gradient, because constants cannot touch it

Stan's `~` statements drop terms that are constant in the parameter, so an *absolute* log-density
comparison would be asserting that convention as much as the density. Differentiation removes the
problem entirely: **additive constants vanish**, so the gradient is convention-free.

And for this model it collapses to a straight line. cmdstanpy reports the gradient with respect
to the *unconstrained* parameter `z = logit(θ)`, so `d/dz = (dlp/dθ)·θ(1−θ)` and the kernel's
rational form cancels:

```text
jacobian=False :  grad_z = (a−1+y)(1−θ) − (b−1+N−y)θ  =  18 − 53θ
jacobian=True  :  grad_z = (a+y)(1−θ)   − (b+N−y)θ    =  19 − 55θ
```

Measured against those two lines at four θ values each, the worst difference is **1.350e-15**
and five of the eight agree **exactly**. Checking both settings is what tests the Jacobian
adjustment itself — the piece that decides whether HMC targets the right distribution at all.

**The log density agrees less tightly, and the reason is precision, not disagreement.** Its
differences match to **7.235e-07**, which is where CmdStan's reported `lp__` runs out of
significant digits — the gradient comes back at full double precision, `lp__` does not. So the
1e-6 tolerance is set by the output's precision, exactly as the
[ObsPy TauP comparison](../obspy/README.md) is set by its reference's print quantum.

### Two MAPs, two different right answers

The gradient identity has a known root, and that makes the optimiser checkable too.
`grad_z(jacobian=True) = (a+y)(1−θ) − (b+N−y)θ` vanishes at `θ = (a+y)/(a+b+N)` — which is the
posterior **mean**. So:

```text
optimize(jacobian=False)  ->  0.3396226500   vs  Beta mode  18/53 = 0.339622641509434
optimize(jacobian=True)   ->  0.3454545700   vs  posterior mean 19/55 = 0.345454545454545
```

One model, two correct and *different* answers, each with a closed form. A recipe that only
checked one of them could not tell the Jacobian flag was being honoured — and "the MAP equals
the mean" is a genuinely surprising-looking result that falls straight out of the algebra.

### The sampler is checked against its own stated uncertainty

A posterior mean estimated from MCMC is not expected to equal the exact value — it is expected to
be within its Monte Carlo standard error. So the tolerance is **taken from the MCSE Stan itself
reports**, not chosen:

```text
exact mean   0.345454545454545      (Beta(19,36), by conjugacy)
sampled mean 0.3453510              MCSE 0.0008262150   ->  0.125 MCSE
```

Four standard errors is the bound asserted — a two-sided event of probability ~6e-5 if the
sampler is correct — and the run came in at **0.125**. The fixed seed makes this deterministic,
so it cannot go flaky; stating the bound in MCSE units is what keeps it a *justified* number
rather than one fitted to the observation.

The spread is checked too (sd within 0.58% of the exact 0.0635435), alongside the health
signals that distinguish "converged" from "ran": R-hat **1.000420**, **zero** divergent
transitions, and CmdStan's own `diagnose` returning *no problems detected* — a completion
sentinel from the tool rather than a threshold invented here.

### Reproducibility comes before any exact claim

HMC is a stochastic algorithm, so none of the sampled numbers above means anything unless the
run is pinned. Two fits at seed `20260101` produce **bit-identical** draws (4000 × 4 chains × 8
columns); a different seed produces different draws, which is *reported rather than asserted* —
had it coincided, that would be a fact about this model, not a failure.

### Pins

| | |
|---|---|
| model | `beta_binomial.stan`, staged and pinned by sha256 |
| data | `data.json` — `N=50, y=17`, prior `Beta(2,3)`; four integers |
| image | `quay.io/aarchsci/bayes@sha256:cfa7cc14…` — cmdstan 2.40, cmdstanpy 1.3.0, python 3.14.8 |

**Nothing scientific is fetched, because the reference is arithmetic.** Conjugacy makes the
posterior `Beta(a+y, b+N-y)` exactly, so there is no dataset to pin and no published table to
reproduce — the staging script derives the mean, sd and mode independently and refuses data that
would put the posterior near a boundary or leave the constrained kernel without an interior mode.

**CmdStan compiles every model with a C++ toolchain at run time**, which is a real dependency and
not an implementation detail: the env carries `g++ 15.3.0` and GNU `make 4.4.1`, and the task
checks for both *before* compiling, because a missing compiler otherwise surfaces as an opaque
`make` error minutes in.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest (the list also carries an `unknown/unknown`
attestation entry, which is not an image).

### Run + verify

```sh
make stage RECIPE=cmdstan
spawn task run --spec "$(make -s spec RECIPE=cmdstan)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/cmdstan/r1/score.tsv" -
```

Fails on a pin mismatch, a missing compiler, a gradient that departs from its analytical line, a
MAP that misses either closed form, a posterior mean more than 4 MCSE from the exact value, a
non-zero divergence count, a diagnostic complaint, or two same-seed runs that differ — but check
the bucket regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Hierarchical and non-conjugate models, where no closed form exists and the honest check becomes
simulation-based calibration rather than an analytical comparison; `variational` and
`laplace_sample`, both present in this build and unexercised; `generate_quantities` and posterior
predictive checks; within-chain threading (`STAN_NUM_THREADS` is unset here) and
`parallel_chains`, so no scaling claim is made; `reduce_sum` and map-rect parallelism; and
multi-parameter geometry — a one-parameter posterior exercises none of the mass-matrix adaptation
that makes HMC interesting on real problems.

</details>
