# ArviZ on all of posteriordb's reference posteriors — Graviton4, Intel and AMD

> **ArviZ 1.3's ESS and R-hat match R's `posterior` package and Stan's `stansummary` to about
> 1e-14 on the same draws: 390 reference scalars and 1,342–1,356 freshly sampled ones, on all three
> chips.** Three things differ, and each is a different definition rather than an error. `az.summary`'s
> `ess_tail` is taken at the 11%/89% quantiles (Stan uses 5%/95%), so it is up to 40% off Stan's.
> `az.mcse` uses split chains, so it is up to 4% off Stan's MCSE. And when a chain never moves,
> `stansummary` reports R-hat = NaN while ArviZ reports 1.52 — and NaN is not > 1.01.

Workload: **every** posterior in [posteriordb](https://github.com/stan-dev/posteriordb) **1.1.0**
that has a reference posterior. That is 57 posteriors over 48 Stan models (regressions, GLMs,
hierarchical models, HMMs, GARCH, Gaussian processes, an ODE). ArviZ diagnoses two sets of draws:

- **R:** posteriordb's own reference draws (10 chains × 1,000 per posterior), with the ESS and R-hat
  its authors computed in R with `posterior`. That gives ArviZ and R the same doubles.
- **F:** fresh CmdStan 2.40 fits of all 57 (4 chains × 1,000, `sig_figs=18`), read by
  `az.from_cmdstanpy` and by Stan's `stansummary`. Every scalar is compared: parameters, transformed
  parameters and generated quantities, 4,545 per chip.

This is the shape of a real ArviZ job: diagnose a sampler's output before trusting it, on the
benchmark set the Stan developers built for that purpose.

**Data tier: a durable public id, a versioned release.** This measurement reuses
[cmdstan-real](../cmdstan-real/README.md)'s staged copy in place. That copy is posteriordb at tag
`1.1.0`, commit `299b77cf`: 256 files, 32 MB, each checked against its git blob. Those pins are
copied into [`pins.sha256`](pins.sha256) here and checked again at every stage-in.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/cmdstan-real/stage-inputs.sh "$B"     # once: posteriordb 1.1.0, if not staged
bash measurements/arviz-real/stage-inputs.sh "$B"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py arviz $a "$B" "$(date +%s)" \
    measurements/arviz-real/real.task.json)" --wait
done
bash measurements/arviz-real/collect.sh "$B"           # results/<chip>/, results/compare.txt
```

[`arviz_real.py`](arviz_real.py) is the workload and its checks. Per scalar it calls:

```python
az.ess(d, method="bulk"); az.ess(d, method="tail", prob=0.95); az.rhat(d); az.mcse(d, method="mean")
az.summary(idata, kind="diagnostics", round_to="none")   # checked equal to the calls above
```

## Results

`m8g.xlarge`, `m8i.xlarge` and `m8a.xlarge` (4 vCPU, 16 GiB). Graviton4 runs the recipe's image.
The two x86 chips run the `bayes` env's linux-64 twin. All three read the same staged bytes.

| chip | instance | ArviZ | whole task | billed | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.xlarge` | 41.8 s | 1,128 s | 1,204 s | 6.93 GiB | 0.1795 | 0.0562 | 0.0600 |
| Xeon 6975P-C | `m8i.xlarge` | 38.9 s | 1,261 s | 1,315 s | 8.87 GiB | 0.2117 | 0.0742 | 0.0773 |
| **EPYC 9R45** | `m8a.xlarge` | **26.8 s** | **704 s** | **743 s** | 8.97 GiB | 0.2434 | **0.0476** | **0.0502** |

A result is the whole verified set: 57 posteriors sampled and both draw sets diagnosed and checked.
"ArviZ" is ArviZ's own time: part R plus part F, conversion included. Peak memory is the cgroup's
`memory.peak`, which includes page cache and the model compiles. The ArviZ process itself peaked at
0.39–0.40 GiB (`ru_maxrss`). Prices are spawn's `cost_estimate` over the billed window.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| ArviZ, part R (390 reference scalars) | 5.6 | 5.2 | 3.7 |
| ArviZ, part F (4,545 scalars, plus two invariance maps each) | 36.2 | 33.7 | 23.1 |
| `stansummary`, 57 fits | 33.5 | 9.0 | 5.2 |
| CmdStan compile 48 models + sample 57 | 1,057 | 1,195 | 657 |

- **ArviZ is 3–4% of the task.** Diagnosing 57 posteriors takes 27–42 s. CmdStan, which produced
  the draws, takes 93–95%. On ArviZ alone AMD is 1.56× faster than Graviton4, and Intel 1.07× faster.
- **AMD is cheapest, as for CmdStan**: 16% under Graviton4 billed, despite an hour 36% dearer.
  Intel is 29% over Graviton4. Intel's 4 vCPU are 2 cores × 2 threads; AMD's and Graviton4's are
  4 full cores (`threads_per_core` 2 / 1 / 1).
- **`stansummary` is 3.7× slower on Graviton4 than on Intel**, on the same CSVs. It is a C++
  binary and runs once per fit, so the time is likely start-up and CSV parsing, not statistics.
  This was not investigated further.
- n = 1 per chip. The workload is deterministic: two Graviton4 runs at the same seed drew
  byte-identical draws for all 57 posteriors, and ArviZ's statistics on fixed bytes are fixed (below).

## What held at scale, on all three chips

Asserted inside the task by [`arviz_real.py`](arviz_real.py). A failure fails the run. The bound is
**1e-9 relative**, and it rests on one argument. Each statistic is a deterministic function of
the same doubles, so two correct implementations of one definition differ only by rounding (about 1e-13
here). The definitional differences measured below start at 1.7e-8. The bound sits between them.

| claim | against | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| `ess_bulk`, 390 reference scalars | R `posterior` (0.0.1, 0.0.2, 1.6.1) | 5.4e-15 | 5.4e-15 | 5.4e-15 |
| `ess(method="tail", prob=0.95)`, same | R `posterior` | 6.1e-15 | 4.7e-15 | 4.7e-15 |
| `rhat`, the 22 scalars computed by `posterior` 1.6.1 | R `posterior` 1.6.1 | 4.9e-15 | 4.9e-15 | 4.9e-15 |
| converged continuous scalars (both tools R-hat < 1.01) | `stansummary` | 1,342 | 1,356 | 1,356 |
| — mean, sd, 5/50/95% quantiles | `stansummary` | ≤ 1.4e-13 | ≤ 1.0e-13 | ≤ 1.0e-13 |
| — `ess_bulk`, `ess_tail` (prob 0.95) | `stansummary` | ≤ 5.0e-15 | ≤ 4.2e-15 | ≤ 4.2e-15 |
| — sd / √(ESS of the unsplit chains) | `stansummary` MCSE | 8.3e-13 | 8.3e-13 | 8.3e-13 |
| R-hat, every continuous scalar with no frozen chain | `stansummary` | 4.5e-16 | 2.2e-16 | 2.2e-16 |
| `ess_bulk`, `ess_tail`, bulk R-hat under 3.7x + 112.5 and under x³ | itself | 0 of 1,356 moved | 0 of 1,356 | 0 of 1,356 |
| every posterior `stansummary` flags (R-hat > 1.01), ArviZ flags | `stansummary` | 1 of 1 | 0 of 0 | 0 of 0 |
| `az.summary` columns equal `az.rhat` / `az.ess` | itself | 57 of 57 fits | 57 of 57 | 57 of 57 |

The invariance holds because those three statistics depend on the draws only through their ranks,
and each map keeps every rank (checked per scalar; 0 skipped). It holds bit for bit on 2 × 1,356
real scalars per chip.

## Where they differ: three definitions, each measured

**`az.summary`'s `ess_tail` is not Stan's.** ArviZ 1.3 takes tail ESS at
`rcParams["stats.ci_prob"]` = 0.89, that is at the 11% and 89% quantiles. Stan and `posterior`
define it at 5% and 95%. `az.summary` does not expose `prob`, and its `ci_prob` argument does not
change it. Against R on the reference draws the default is up to **11.8%** off. Against
`stansummary` on fresh fits it is off on every converged scalar, by up to **34–40%**. With `prob=0.95` the
two agree to 6e-15. So to compare with a Stan workflow, call `az.ess(..., method="tail", prob=0.95)`.

**`az.mcse` uses a different ESS from `stansummary`'s MCSE.** ArviZ computes sd/√ESS with the
chains split. `stansummary` uses the unsplit chains. On converged scalars the two differ by up to
**4.2%** (Graviton4) and 4.1% (x86). Stan's definition, computed from ArviZ's own pieces, agrees with
`stansummary` to 8.3e-13, so the difference is the definition alone.

**A chain that never moves.** At seed 20261020 a chain of `arma-arma11` never moved: all 1,000 of
its draws are identical, `lp__` included, on every chip (the same bytes). `stansummary` reports R-hat
**NaN** and ESS NaN for all five scalars. ArviZ reports R-hat **1.5245879356513925** for `lp__` and
1.524–1.525 for the rest, which flags it. NaN fails no `> 1.01` test, so a filter on `stansummary`'s
column passes the worst failure in the set. The assertion "ArviZ flags every posterior `stansummary`
flags, and flags more only where `stansummary` says NaN" holds on all three chips.

Reported, not asserted:

- **R on the old `posterior` releases.** 115 of the 368 reference scalars computed by `posterior`
  0.0.1/0.0.2 (2020) have an R-hat **1.7e-8 to 4.6e-6** from ArviZ's. In all 115 the folded half of R-hat is
  the larger, and the bulk half agrees exactly. So those early releases computed the folded half
  slightly differently. `posterior` 1.6.1 matches to 4.9e-15.
- **An unconverged posterior.** On Graviton4 the HMM `bball_drive_event_0-hmm_drive_0` has R-hat up to
  1.53 in both tools (R-hat agrees to 4.5e-16). There `stansummary`'s `ESS_tail` is **exactly 2000.0**
  (N/2) on 10 of 14 scalars, where ArviZ gives 27–33. ESS_bulk differs by 0.2%. ESS of chains that disagree means
  nothing, and both tools say they disagree. But 2000.0 is a number that would look healthy in a table.
  On x86 this posterior's chains mixed, and the two tools agree on it to 1e-15.
- **Integer-valued scalars** (posterior-predictive `y_rep`, HMM states `z_star`): 723 (Graviton4) and
  783 (x86). Ties dominate their ranks. `stansummary` returns R-hat NaN on 282 / 335 of them, and ArviZ
  returns a number.
- **The folded half of R-hat is not rank-invariant.** It folds about the median, `|x − median|`.
  A non-affine map such as x³ changes that ordering, so the reported R-hat (the larger half) moved in
  1,000–1,033 of 1,356 scalars, by up to 4.3e-3. Under 3.7x + 112.5 it moved by up to 8.6e-5, because that map is
  not exact in floating point and the folded half orders near-equal distances. The
  [recipe](../../recipes/arviz/README.md)'s cube invariance holds on its own fixture, where the bulk
  half dominates. It is not a property of R-hat in general.

## Across chips: fixed point and path

**ArviZ on the reference draws is a fixed point.** It agrees across chips to **2.1e-15**, and
`compare.py` fails above 1e-12. Graviton4 matches the x86 chips bit for bit in 332 of 390 scalars. The
rest differ in the last bits, through numpy's FFT. The `posterior`-version split above is the same on every chip.

**The fresh draws are a path.** 24 of 57 posteriors drew byte-identical draws on all three chips.
That is the same 24 as [cmdstan-real](../cmdstan-real/README.md), at a different seed. On those 24
(3,155 scalars), every ArviZ and `stansummary` value agrees across chips to 4.4e-15. On the other 33
the draws differ, so the conclusions can differ too: Graviton4's HMM failed to mix, and the x86
chips' did not. Each chip passed every assertion on its own draws, which is the claim that matters
for a path. Full output is in [`results/compare.txt`](results/compare.txt).

**Intel and AMD are bit-identical**: all 57 fits' draws and all 4,545 × 2 values. They run the same
linux-64 build. `openblas_get_corename()` is `neoversev2` on Graviton4 and `Cooperlake` on both x86
chips. ArviZ's statistics make no large BLAS calls.

## Caveats

- One configuration of each: 4 × 1,000 fresh draws, and posteriordb's 10 × 1,000 thinned references.
  `loo`/`waic`, `hdi`, plotting and multi-group `InferenceData` were not exercised.
- The stuck `arma` chain is a property of seed 20261020. [cmdstan-real](../cmdstan-real/README.md)'s
  seed sampled it cleanly. It is kept because it is the case the diagnostics exist for.
- The specs ran with `cost_limit` 0.5 (Graviton4, scaled by spec_arch for x86) and a 3 h TTL as the
  backstop. Sized from cmdstan-real's measured wall on the same instances.

## Spend

Seven launches, **$0.33** in all. The three verifying runs cost $0.19. Two probes ($0.02) and two
Graviton4 runs ($0.12) found the scalars the checks had to handle: integer-valued quantities, and a
chain that never moves.
