---
tool: scvelo
tool_version: "0.3.4"
env: single-cell
image: quay.io/aarchsci/single-cell@sha256:14d4af5bbd86625e3c98eb7b2078e3da6206aa9cb1ea3a3d631e96c7bebb901a
gap_x86_64: the aarch.science `single-cell` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-16) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-16
---
# scVelo — RNA velocity on counts with known kinetics, steady-state and dynamical

Simulates spliced/unspliced counts from the splicing ODEs with planted per-gene rates, runs scVelo's steady-state and dynamical models on Graviton4, and checks each against a closed form or the planted truth. For anyone doing RNA velocity on ARM.

## Run it

```bash
make stage RECIPE=scvelo      # once: the checks only — the counts are simulated
spawn task run --spec "$(make -s spec RECIPE=scvelo)" --wait
make ls RECIPE=scvelo

scv.tl.velocity(adata, mode="deterministic")                          # gamma == numpy, velocity == Mu - gamma*Ms
scv.tl.recover_dynamics(adata, var_names="all", t_max=False, n_jobs=1) # t_max=False: see below
scv.tl.velocity(adata, mode="dynamical_residuals")                    # mode="dynamical" fails here
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| a `make_unique_list` shim | the plain call | **`recover_dynamics` fails on this env's pandas 3.0.6** before fitting (`TypeError: unique requires a Series…`: it hands `pandas.unique` a list). The recipe swaps in the same order-preserving de-duplication; the run probes the plain call first and records it. |
| `recover_dynamics(..., t_max=False)` | the default | the default then dies in `align_dynamics` (`assignment destination is read-only`: pandas 3 `.values`). `t_max=False` skips only the time alignment, which rescales a gene's α, β, γ and time by one factor, so **γ/β and phase calls are unchanged** — absolute rates and `fit_t` are not on a common clock. |
| `mode="dynamical_residuals"` | `mode="dynamical"` | `"dynamical"` fails the same way (`output array is read-only`). Residuals are β·u − γ·s from the fitted rates. Downstream `velocity_graph` and plotting are not exercised. |
| `scv.datasets.simulation`, 1,000 cells × 100 genes | your loom / h5ad | needs `spliced` and `unspliced` layers; nothing is fetched. Your rates are unknown, so only the exact identities carry over. |

**Leave the fixture.** The point is a truth to recover; real data has none. **Scale it** for runtime only — the dynamical fit is per gene, and the identities hold at any size.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB; 0.6 GB peak RSS measured), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. The fit takes seconds of the window ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: the simulator solves the ODEs to 3e-16, gamma and velocity equal a numpy recompute, planted gamma/beta ranked at 0.998, phase called at 84% against a 56% constant guess</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| scvelo / scanpy / pandas / numpy | read from the running install | **0.3.4** / 1.12.4 / 3.0.6 / 2.5.4 |
| cell and switch times, reconstructed | `== 0` vs `true_t`, `true_t_` | 0.0e+00 / 0.0e+00 |
| **noise-free simulator vs closed-form ODE solution** | **rel `< 1e-12`** | **u 0.00e+00, s 2.75e-16** |
| **Ms, Mu vs neighbourhood means** | **rel `< 1e-6`** | **1.06e-07 / 1.07e-07** |
| **steady-state γ vs numpy extreme-quantile slope** | **rel `< 1e-5`** | **3.69e-07** over 100 genes |
| **velocity vs Mu − γ·Ms** | **`== 0` exactly** | **0.000e+00** |
| **steady-state γ ranks planted γ/β** | **Spearman `> 5/√(G−1)` = 0.503** | **0.9885** |
| **dynamical γ/β ranks planted γ/β** | **Spearman `> 0.503`** | **0.9977** |
| **induction/repression calls vs planted phase** | **beat constant guess + 5σ** (0.5724) | **0.8442** of 100,000 |
| **velocity sign vs true ds/dt**, steady-state / dynamical | **`> 0.5 + 5σ`** (0.5079) | **0.8715 / 0.9270** |
| probe: `recover_dynamics` as documented | recorded, not asserted | raises `TypeError` |
| probe: with alignment / `mode="dynamical"` | recorded, not asserted | raise `ValueError` (read-only) |

### Why these bounds

The exact checks are exact for a reason. The simulator's noise-free output is compared with the
ODE solution written out independently, at cell times rebuilt from the simulator's own documented
draw — the same expression in float64, so `1e-12`. Moments are a neighbourhood mean and γ a
through-origin least-squares slope on the cells above the 95th percentile of `Ms/max + Mu/max`;
both are float32 in scVelo, so their bounds are float32 round-off. The steady-state velocity is
defined as `Mu − γ·Ms` and is asserted bit-identical.

Recovery has no exact answer, so each claim is stated against its null, never as a band on this
run. A Spearman correlation under no recovery has sd `1/√(G−1)`, so 0.503 is 5 sigma at 100
genes. Phase calls are measured against the best constant guess (the more common phase holds 56% of
cell-gene pairs) plus 5 binomial sigma. Rates are drawn over a decade each so the ranks are not a
near-tie. γ/β is the identifiable quantity: the steady-state slope is γ/β by definition, and the
time alignment skipped above rescales β and γ together.

### Pins

| | |
|---|---|
| data | **none staged** — `scv.datasets.simulation(n_obs=1000, n_vars=100, t_max=25, random_seed=0)` with α, β, γ log-uniform over a decade and switch points uniform in 30–80% of t_max, from `default_rng(20261016)` |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/single-cell@sha256:14d4af5bbd86…` — scvelo 0.3.4, scanpy 1.12.4, squidpy 1.8.2, pandas 3.0.6, numpy 2.5.4 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`c422f5f8c974…`), so verify that and pin the arm64 entry. Same image as
[squidpy](../squidpy/README.md). No x86 run: `single-cell` has no x86 build.

### Run + verify

```sh
make stage RECIPE=scvelo
spawn task run --spec "$(make -s spec RECIPE=scvelo)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/scvelo/r1/score.tsv" -
```

Fails on a pin mismatch, a simulator that does not solve the ODEs, moments that are not
neighbourhood means, a γ or velocity off its definition, a recovery statement that does not beat
its 5-sigma null — or a NaN anywhere a bound is applied. The three probes are recorded and never
fail the run, so a fixed digest shows up as `works`. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real data**: no loom from velocyto or STARsolo is read. **The stochastic model** (the default
`mode`), **latent time**, **`velocity_graph`**, embedding projection and **every plot**. **Aligned absolute rates**, skipped above.

</details>
