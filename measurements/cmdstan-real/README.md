# CmdStan on all of posteriordb's reference posteriors — Graviton4, Intel and AMD

> **CmdStan 2.40 samples all 57 posteriordb posteriors that ship reference draws, on all three chips.
> Every posterior that CmdStan reports as converged matches the published reference at all 1,170
> quantile checks.** AMD EPYC runs the set 1.9× faster than Graviton4 and is 26% cheaper billed.
> Intel is the slowest and the dearest. The log density at fixed points agrees across chips to
> 6e-15. The draws are a path: 24 of the 57 posteriors give byte-identical draws on all three chips.

Workload: **every** posterior in [posteriordb](https://github.com/stan-dev/posteriordb) **1.1.0**
that has a reference posterior. That is 57 posteriors over 48 Stan models: regressions, GLMs,
hierarchical models, HMMs, GARCH, Gaussian processes, an ODE (Lotka–Volterra) and a
pharmacokinetic model. Each posterior is sampled the way a researcher runs Stan: 4 chains,
1000 warmup and 1000 draws, with the reference's own `adapt_delta`/`max_treedepth` where it set one.
posteriordb exists to benchmark inference against these posteriors, so this is the set to use, not a
slice of it.

**Data tier: a durable public id, a versioned release.** posteriordb 1.1.0 is tag object `193fcfc`,
commit `299b77cf`. [`manifest.tsv`](manifest.tsv) lists the 256 files the 57 posteriors need (32 MB):
models, data, reference draws and reference info. [`00-fetch.task.json`](00-fetch.task.json) fetches
them on AWS at that commit. It keeps a file only if its git blob id equals the one in the release
tree. [`pins.sha256`](pins.sha256) pins them again at every stage-in.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/cmdstan-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if not yet staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py cmdstan $a "$B" "$(date +%s)" \
    measurements/cmdstan-real/real.task.json)" --wait
done
bash measurements/cmdstan-real/collect.sh "$B"           # results/<chip>/, results/compare.txt
```

[`real.py`](real.py) is the workload and its checks. The call it makes per posterior is:

```python
fit = model.sample(data=data_file, chains=4, parallel_chains=1, seed=20261018,
                   iter_warmup=1000, iter_sampling=1000, sig_figs=18, **reference_control)
```

## Results

`m8g.xlarge`, `m8i.xlarge` and `m8a.xlarge` (4 vCPU, 16 GiB). The recipe's image runs on Graviton4,
and the `bayes` env's linux-64 twin runs on the two x86 chips. All three read the same staged bytes
and run 4 worker processes, one chain at a time each:

| chip | instance | tool wall | billed | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.xlarge` | 1,494 s | 1,606 s | 6.87 GiB | 0.1795 | 0.0745 | 0.0801 |
| Xeon 6975P-C | `m8i.xlarge` | 1,900 s | 2,031 s | 8.77 GiB | 0.2117 | 0.1117 | 0.1194 |
| **EPYC 9R45** | `m8a.xlarge` | **780 s** | **881 s** | 8.91 GiB | 0.2434 | **0.0527** | **0.0596** |

A result is the whole verified set: 48 compiles and 57 posteriors sampled twice, plus every check
below. Peak memory is the cgroup's `memory.peak`, which includes page cache. The largest single
process was 1.86–1.92 GiB (`ru_maxrss`).

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| compile 48 models (4 at a time) | 168 | 221 | 136 |
| sample 57 posteriors × 2 (4 at a time) | 1,326 | 1,679 | 644 |
| `diamonds` alone, one run | 1,253 | 1,678 | 562 |
| **24 posteriors with byte-identical draws on all three chips, both runs** | **498** | **654** | **322** |

- **One posterior sets the wall clock.** `diamonds` (26 parameters, `adapt_delta` 0.99, treedepth 20)
  takes 87–100% of the sampling phase on every chip. Its 4 chains run serially inside one worker,
  so the job ends when `diamonds` ends.
- **Compare like with like on the last row.** Those 24 posteriors drew the same bits on every chip,
  so each chip did the same leapfrog steps. On that work AMD is 1.55× Graviton4 and Graviton4 is
  1.31× Intel. On the whole job AMD is 1.92× Graviton4. Part of that larger gap is a different
  path, not a faster chip (see below).
- **AMD's hour is 36% dearer than Graviton4's**, and it is still 29% cheaper in compute per result
  and 26% cheaper billed. Intel is 49% dearer billed than Graviton4. Intel's 4 vCPU are 2 cores ×
  2 threads, so 4 single-threaded chains share 2 cores. AMD's and Graviton4's are 4 full cores.
- **Overhead is 101–131 s of each bill.** Graviton4 pulls the aarch.science `bayes` image (16 s).
  The x86 twin installs the same versions offline into a small micromamba image.
- n = 1 per chip. Each posterior is run twice at the same seed on every chip, and the draws are
  byte-identical both times (57 of 57), so the work per chip is fixed.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py). A failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| 57 posteriors, 48 models; every reference parameter present in the fit | the release | 390 / 390 | 390 / 390 | 390 / 390 |
| **every converged posterior: at q = 0.05, 0.5, 0.95, the fraction of draws below the reference quantile is within 5 SE of q** | **the reference posterior** | **max \|z\| 3.76** | **max \|z\| 3.54** | **max \|z\| 3.54** |
| quantile tests, all posteriors | | 1,170 | 1,170 | 1,170 |
| converged: R-hat < 1.01 on every parameter, 0 divergent, `diagnose` clean | CmdStan's own verdict | 55 of 57 | 54 of 57 | 54 of 57 |
| same seed, run twice | determinism | 57 of 57 identical | 57 of 57 | 57 of 57 |

**The check is a quantile test, not a mean ± MCSE.** For each parameter, take the reference's
q-quantile and count the fraction of our draws below it. If our draws come from the same posterior,
that fraction is q within `sqrt(q(1−q)(1/ESS + 1/ESS_ref))`. ESS is `min(ESS_bulk, ESS_tail)`:
CmdStan's own for our draws, and posteriordb's published one for the reference. The test needs no
finite variance. A mean ± MCSE needs one, and one posterior breaks it.
`one_comp_mm_elim_abs`'s `K_m` has reference mean 2.54, median 0.98 and maximum 296. Its sampled mean
is 4.04 MCSE from the reference. That is reported, not asserted, and it is the worst mean z in the
set on all three chips. Its quantile z-scores are 1.52, 0.88 and 2.38. With 1,170 tests a 5-SE bound gives a
chance of about 6e-4 of any false failure in the set. It still catches a shift of about 0.1 in
quantile probability.

**What is asserted is "no silent miss".** A posterior CmdStan flags is reported, not checked against
the reference:

| flagged by CmdStan | Graviton4 | Xeon / EPYC | max \|z_q\| |
|---|---|---|---|
| `bball_drive_event_0-hmm_drive_0` | R-hat 1.047, 35 divergent | R-hat 1.060, 112 divergent | 1.74 / 1.77 |
| `earnings-logearn_height_male` | R-hat 1.0116 | — | 1.16 |
| `earnings-logearn_logheight_male` | — | R-hat 1.0104 | 1.64 |
| `nes_logit_data-nes_logit_model` | — | R-hat 1.0106 | 2.91 |

Which posteriors CmdStan flags depends on the path, so it differs between the arm64 and x86 builds.
The HMM is flagged on every chip, with one chain in a minor mode: `lambda[2]`'s sd is 63× the
reference's on Graviton4. The other three only just cross 1.01. All four still sit within 3 SE of
the reference.

Reported, not asserted: the RMS quantile z over converged posteriors is 0.928 (Graviton4) and 0.925
(x86). For a correct sampler it should be about 1. 3.9% and 3.6% of the tests exceed 1.96, against
5% expected. 0 tree-depth saturations anywhere. Two posteriors (`eight_schools_noncentered`,
`gp_pois_regr`) store only transformed parameters in their reference draws, so the log-density
comparison below skips them.

## Across chips: what is a fixed point and what is a path

**The log density is a fixed point, and it agrees.** [`compare.py`](compare.py) evaluates `lp__` and
its gradient (`log_prob`, `jacobian=True`) at the first 20 reference draws of 55 posteriors. That is
1,100 points, compared across chips. Against Graviton4, both x86 chips match bit for bit at 751 of
1,100 points. Elsewhere `lp__` differs by at most **6.1e-15** relative, and the gradient by
**1.9e-13** of its own scale (both at `sblrc-blr`). `compare.py` fails on more than 1e-12 and 1e-10.
That is rounding in a sum of at most a few thousand terms, far below any wrong density. The model
binaries link no BLAS (`libtbb`, `libstdc++`, `libm` only), so OpenBLAS kernel choice plays no
part here.

**The draws are a path.** NUTS amplifies a last-bit difference in the gradient into a different
trajectory. 24 of the 57 posteriors give byte-identical draws on all three chips, at 18 significant
digits. The other 33 diverge between the arm64 and x86 builds. Posterior means are identical in 88
of 390 parameters. The largest difference is 0.35 reference sd (the flagged HMM's `lambda[2]`).
Among converged posteriors every chip passes the same reference check independently, which is the
cross-chip claim that matters for a path. The full comparison is in
[`results/compare.txt`](results/compare.txt).

**Intel and AMD are byte-identical to each other**: all 57 posteriors' draws, all 1,100 log
densities and every statistic computed from them. They run the same linux-64 build. So the cross-chip
differences here come from the arm64 and x86 builds, not from the vendor.

## Caveats

- One configuration: 4 × 1000 draws at CmdStan's defaults. posteriordb's references are 10 × 10,000
  thinned to 10,000, so the reference adds little to the test's variance. `variational`,
  `pathfinder`, `laplace` and within-chain threading were not run.
- `parallel_chains=1` with 4 workers keeps the 4 cores busy (3.93–3.96 busy cores). It makes
  `diamonds` the critical path. A run with `diamonds`' chains in parallel would finish sooner on
  every chip but was not measured.
- The specs ran with `cost_limit` 0.5 (Graviton4) and the x86 scaling of it, and a 3 h TTL as the
  backstop. Prices are spawn's own estimate for these instances, on demand in us-west-2.

## Spend

Six launches, about **$0.47** in all. The three verifying runs cost $0.25. The rest was the fetch
and two Graviton4 sizing runs, which found two shape quirks in posteriordb's info files (below).

posteriordb 1.1.0 is not uniform. 11 of the 57 reference info files spell the diagnostics key
`diagnostics_information` and 46 spell it `diagnostic_information`. A one-parameter posterior
(`Rate_1`, `Rate_3`, `Rate_5`) stores its names and ESS as scalars, not one-element lists.
[`real.py`](real.py) reads both forms.
