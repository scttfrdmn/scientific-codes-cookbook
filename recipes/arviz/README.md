---
tool: arviz
tool_version: "1.3.0"
env: bayes
image: quay.io/aarchsci/bayes@sha256:583447048ee2ed011ec822aa15a68c5248842c5b22959bdf98da5d3e9302b46d
spawn_version: 0.126.1
last_verified: 2026-10-11
---
# ArviZ — R̂ bit-identical under a cube, and ESS against a closed form

Checks the MCMC diagnostics themselves on Graviton4, using chains with known autocorrelation so the right answers are derivable. For anyone doing Bayesian workflow on ARM.

## Run it

```bash
make stage RECIPE=arviz     # once: the checks only — the draws are synthesised
spawn task run --spec "$(make -s spec RECIPE=arviz)" --wait
make ls RECIPE=arviz

d = az.convert_to_dataset({"theta": draws})     # (chain, draw); NOT az.from_dict
az.rhat(d)["theta"]        # 1.00039004683446020 — and identical for exp(θ) and θ³
az.ess(d)["theta"]         # ESS/N → (1−ρ)/(1+ρ) for an AR(1) chain
az.mcse(d)["theta"]        # == sd/√ess, to 7.6e-05
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `az.convert_to_dataset` | `az.from_dict` | **`from_dict` wants a dict of *groups*** (`{"posterior": {...}}`); a flat dict raises `AttributeError: 'numpy.ndarray' object has no attribute 'items'`. arviz 1.x is a rewrite — `InferenceData` is now xarray's `DataTree`. |
| synthetic AR(1) draws | a real sampler's draws | you lose the closed form. The point of AR(1) is that `(1−ρ)/(1+ρ)` says what ESS *should* be, so the estimator is the thing under test. |
| the ρ = −0.5 rung | positive ρ only | **keep it.** It is the rung that shows **ESS > N** is legitimate — 2.774 against a closed form of 3.0. |
| the badly-mixed control | the invariance alone | without it, "R̂ is invariant" would also hold for an estimator that returns a constant. |
| these diagnostics | posterior values | [cmdstan](../cmdstan/README.md) already checks a posterior against conjugate arithmetic; this page is about the estimators, not the model. |

**Leave the fixtures.** Nothing is fetched and no sampler runs: the chains are built with a chosen autocorrelation so every reference is an exact invariance or a closed form. **Scale it** by pointing the same checks at your own `InferenceData` — the invariance travels, the closed form doesn't.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All checks take **7 s** of a 1m08s window; 38 s is installing Docker and 22 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: an exact invariance under a cube, a five-rung closed form, and a false invariant deliberately not asserted</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| arviz / numpy | read from the running install | **1.3.0** / 2.5.3 |
| **R̂ under an affine map** | **diff `== 0` exactly** | **0.000e+00** |
| **R̂ under `exp`** | **diff `== 0` exactly** | **0.000e+00** |
| **R̂ under a cube** | **diff `== 0` exactly** | **0.000e+00** |
| **badly-mixed chains** | **R̂ > 1.5** | **1.732213** (vs 1.000390) |
| **ESS vs `(1−ρ)/(1+ρ)`, 5 rungs** | **within 30%** | **7.5%** worst |
| **antithetic ρ = −0.5** | **ESS/N > 1** | **2.774** (closed form 3.0) |
| **`mcse` vs `sd/√ess`** | **< 1e-3 relative** | **7.592e-05** |
| "ESS ≤ total draws" | *deliberately **not** asserted* | false — see below |

### The exact one: R̂ cannot move under a monotone map

Rank-normalised split-R̂ is computed from the **ranks** of the draws, so only their ordering can
affect it. Measured on the same chains transformed three ways:

```text
R̂(θ)                   1.00039004683446020
R̂(3.7θ + 112.5)        1.00039004683446020     diff 0.000e+00
R̂(exp θ)               1.00039004683446020     diff 0.000e+00
R̂(θ³)                  1.00039004683446020     diff 0.000e+00
```

Bit-identical to seventeen digits, including under a **cube** — a nonlinear monotone map. An
implementation that normalised wrongly, or that fell back to a non-rank variance ratio, could not
satisfy this however plausible its output looked. Same shape as Moran's I affine invariance in
[geo-ml](../geo-ml/README.md), one field over.

**And the control is what makes it a result.** An estimator that simply returned a constant would
also be "invariant". So chains drawn from N(0,1) and N(5,1) are run through the same code and must
come back far above 1 — **1.732213**, against **1.000390** for the well-mixed case. The invariance
is a property of an estimator that still detects chains which genuinely disagree.

### ESS against a closed form, on both sides of zero

An AR(1) chain with lag-1 correlation ρ has integrated autocorrelation time `(1+ρ)/(1−ρ)`, so
`ESS/N → (1−ρ)/(1+ρ)`. That is a theoretical reference for the estimator itself rather than a band
on an observed number:

```text
   ρ      ESS/N measured   closed form   ratio
 -0.5         2.7740          3.0000      0.925
  0.0         1.0070          1.0000      1.007
  0.5         0.3311          0.3333      0.993
  0.8         0.1063          0.1111      0.957
  0.9         0.0535          0.0526      1.016
```

**The 30% bound is loose, and the justification is what it must catch rather than where the run
landed.** An estimator that ignored autocorrelation entirely would report `ESS/N ≈ 1` at every ρ —
a ratio of **9.0** at ρ = 0.8. So 1.30 is roughly 30× tighter than the error it exists to detect,
while absorbing the estimator's own finite-sample bias (worst observed 7.5%). Staging separately
confirms the synthetic process really has the correlation the closed form assumes — lag-1
correlations of +0.0053, +0.4966, +0.8074, +0.9008 against their parameters — because otherwise
arviz would be measured against the closed form for a *different* process.

### A false invariant, deliberately not asserted

**"ESS ≤ total draws" looks like a structural bound and is not one.** Negatively autocorrelated
(antithetic) chains legitimately give more effective samples than draws, and NUTS is often
antithetic in practice. The ρ = −0.5 rung above demonstrates it: **ESS/N = 2.774** against a closed
form of **3.0**.

A first version of this recipe asserted the bound, and it passed on a fixture that happened to sit
below 1. A second version reported the claim in prose while quoting 0.9703 — a number that does not
support it. The rung is the fix: the claim is now *measured*, with a closed form, by the same
machinery that validates the positive-ρ cases.

### The algebraic relation, which is a near-identity and said so

`mcse_mean` should be `sd/√ess_mean`, a relation among three separately computed outputs. It holds
to **7.592e-05** — four decimal places, not exactly, because arviz's internal variance estimate is
not the plain pooled sample sd. Asserted at 1e-3 and reported with the residual, rather than
claimed as an equality it does not satisfy.

### Pins

| | |
|---|---|
| data | **none.** Chains from `default_rng(20261011)` with chosen autocorrelation; references are an invariance and a closed form |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/bayes@sha256:583447048ee2…` — arviz 1.3.0, cmdstan 2.40.0, cmdstanpy 1.3.0, xarray 2026.9.0, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
make stage RECIPE=arviz
spawn task run --spec "$(make -s spec RECIPE=arviz)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/arviz/r1/score.tsv" -
```

Fails on a pin mismatch, an R̂ that moves at all under a monotone transformation, badly-mixed
chains that do *not* raise R̂ above 1.5, an ESS more than 30% from the AR(1) closed form at any
rung, an antithetic rung that fails to exceed 1, or an `mcse` departing from `sd/√ess` by more than
1e-3 — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Anything about a real posterior.** [cmdstan](../cmdstan/README.md) in this same env checks a
Beta-Binomial posterior against conjugate arithmetic, including its own R̂ and MCSE; this page
deliberately does not restate that, and does not cross-check arviz against CmdStan's
`stansummary` — which would be a worthwhile addition and needs the sampler run.

Also: **plotting**, which is most of what ArviZ is used for and needs a display and image
comparison to verify meaningfully. **`loo` / `waic` and model comparison**, where the reference
would be a second implementation rather than a closed form. **Posterior predictive checks**,
`hdi`, and the `InferenceData`→`DataTree` migration beyond the one constructor used here.
**Multi-dimensional and multi-variable datasets** — one scalar parameter exercises the estimators
but not the group/coordinate handling that makes ArviZ useful on real models.

</details>
