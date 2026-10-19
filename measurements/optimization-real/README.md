# HiGHS, SCIP and CBC on all of Netlib LP and the MIPLIB 2017 benchmark set — Graviton4, Intel and AMD

> **All 90 Netlib LPs come out within 1e-6 of their published exact optima from all three solvers,
> and every MIPLIB answer is consistent with the published optimum on all three chips.** That
> covers 240 instances × 2 solvers: each finished optimum is reproduced, each stopped run's bracket
> contains the optimum, and each incumbent passes MIPLIB's solution checker. One published value,
> binkar10_1's, is itself a tolerance artifact. AMD EPYC solves the most instances within the
> time limit (85 + 71) and Intel the fewest (73 + 66). Graviton4 is cheapest per solved instance.
> SCIP takes the same search path on every chip. HiGHS takes a different one on arm64.

Workload: the two standard test sets for LP and MIP solvers, whole.

- **Netlib LP:** all 91 instances in COIN-OR's
  [Data-Netlib](https://github.com/coin-or-tools/Data-Netlib) at tag `releases/1.2.11`, solved by
  HiGHS, SCIP and CBC. 90 of them have a reference value. That reference is SoPlex's committed
  optimum (`check/testset/quick.solu` at `v8.1.0`, 32 digits). STANDGUB has none: netlib publishes
  no value for it.
- **MIPLIB 2017 benchmark:** all 240 instances of `benchmark-v2.test`. HiGHS and SCIP each solve
  every instance once, single-threaded, under a 300 s wall-clock limit. The reference is MIPLIB's
  solution file `miplib2017-v37.solu`: 232 instances are `=opt=`, 7 are `=inf=`, and 1 is `=best=`.

This is the dataset solver papers report on. A time limit is the test set's own convention (MIPLIB's
engine defaults to 3600 s). At 300 s, a third of the set finishes.

**Data tier: a durable public id.** The test list and the solution file are versioned, and the two
git sources are immutable tags. `benchmark.zip` (333 MB) and netlib's readme have no version in
their URL, so they are pinned by sha256 on first fetch. [`00-fetch.task.json`](00-fetch.task.json)
downloads everything on AWS, and [`pins.sha256`](pins.sha256) is checked again at every stage-in. A
second fetch 30 minutes later gave identical bytes.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/optimization-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if not staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py optimization $a "$B" "$(date +%s)" \
    measurements/optimization-real/real.task.json)" --wait
done
bash measurements/optimization-real/collect.sh "$B" <scratch dir>   # results/, results/compare.txt
```

[`driver.py`](driver.py) runs 64 single-threaded solves at a time, largest file first.
[`solve.py`](solve.py) is one solve. Its settings are the
[MIPLIB 2017 test engine's](https://miplib.zib.de/download.html): gap 0, a wall-clock limit, SCIP's
memory limit at 8 GiB, and one thread. [`summarize.py`](summarize.py) holds the checks. The solver
calls are the plain ones:

```python
h = highspy.Highs(); h.setOptionValue("threads", 1); h.setOptionValue("time_limit", 300)
h.setOptionValue("mip_rel_gap", 0.0); h.readModel(f); h.run()
m = pyscipopt.Model(); m.setParam("limits/time", 300); m.setParam("limits/gap", 0.0); m.readProblem(f); m.optimize()
```

## Results

`r8g.16xlarge`, `r8i.16xlarge` and `r8a.16xlarge` (64 vCPU, 512 GiB), with 64 solves at a time. The
box is sized at 8 GiB per solve, the MIPLIB engine's default memory limit. Graviton4 runs the
recipe's image, and the two x86 chips run the `optimization` env's linux-64 twin. All three use the
same staged bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result | MIPs finished in 300 s | billed $ / finished |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.16xlarge` | 1,872 s | 1,951 s | 115.4 GiB | 3.7702 | 1.961 | 2.043 | 79 + 70 | **0.01371** |
| Xeon 6975P-C | `r8i.16xlarge` | 1,941 s | 1,993 s | 111.7 GiB | 4.4454 | 2.397 | 2.461 | 73 + 66 | 0.01771 |
| EPYC 9R45 | `r8a.16xlarge` | 1,852 s | 1,899 s | 120.2 GiB | 5.1124 | 2.630 | 2.697 | **85 + 71** | 0.01729 |

A result is the whole verified run: 273 LP solves and 480 MIP solves, with every check below.
Finished counts are HiGHS + SCIP: optimal, or infeasibility proved. Peak RSS is the box's anonymous
memory with 64 solves running. The largest single solve was SCIP at 7.0 GiB, and three SCIP solves
stopped at the 8 GiB limit.

- **The wall clock is set by the time limit, not by the chip.** About 160 of the 240 instances run
  to 300 s on every chip, so all three bills land within 5% of each other. The chip's speed shows up
  in how many instances finish inside the limit. Billed $ per finished instance is the column that
  ranks them.
- **On the instances both chips finish, AMD needs 0.93× (HiGHS) and 0.84× (SCIP) of Graviton4's
  summed solve time.** Intel needs 1.43× and 1.36×. Intel's 64 vCPU are 32 cores × 2 threads, so two
  solves share each core. AMD's and Graviton4's 64 vCPU are 64 full cores.
- **Graviton4 is the cheapest per finished instance.** AMD finishes 7 more MIPs (+5%) for an hour
  that costs 36% more. Intel finishes 10 fewer for 18% more.
- **Netlib LP is under 90 s of job time across all three solvers.** At legible scale the LP set is
  a correctness suite, not a load. MIPLIB is the workload.
- **The measured footprint fits an `m8g.16xlarge` (256 GiB).** Peak anonymous memory was 114–121 GiB,
  plus 11 GiB of tmpfs. That box would cost about 24% less. It was not run, and it gives up the
  8 GiB-per-solve guarantee.
- **The runs are deterministic, so n = 1 per chip.** Three Graviton4 runs and two AMD runs gave the
  same finished sets, bit-identical objectives and identical node counts for every finished instance.
  The summed time differed by 0.6%. Intel's two runs differed by one instance: SCIP finished ex10
  near the limit in one run and not in the other.

## What held at scale, on all three chips

Every row is asserted inside the task by [`summarize.py`](summarize.py). A failure fails the run.

| claim | rule | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| **LP: each solver within 1e-6 of SoPlex's exact optimum, 90 instances** | SoPlex's own harness, `check/evaluation.py` | **HiGHS ≤ 9.8e-11, SCIP ≤ 2.6e-10, CBC ≤ 5.9e-8** | HiGHS, SCIP ≤ 2.6e-10; CBC ≤ 4.8e-10 | same as Xeon |
| LP: three solvers agree, same rule | cross-code | 5.9e-8 | 4.8e-10 | 4.8e-10 |
| LP: HiGHS KKT residuals ≤ 1e-7 | its own tolerances | primal ≤ 4.9e-8, dual ≤ 9.6e-8 | ≤ 3.2e-8, ≤ 9.6e-8 | same as Xeon |
| LP: SCIP primal bound == dual bound | strong duality | 0 on all 90 | 0 | 0 |
| LP: CBC's final certificate | see below | 90 / 90 | 90 / 90 | 90 / 90 |
| **MIP: a finished solve matches the `.solu` optimum** | MIPLIB engine, `parse.awk` | **78 + 68 instances** | **72 + 64** | **84 + 69** |
| MIP: no infeasibility claim on an instance with a known solution | MIPLIB engine | 0 | 0 | 0 |
| MIP: both solvers finished and agree | cross-code, same rule | 58 instances, ≤ 2.5e-11 | 54 | 58 |
| **MIP: every reported solution passes the solution checker** | MIPLIB `solchecker` rule, 1e-4 | **397 solutions** | 384 | 399 |
| **MIP: a stopped run brackets the optimum, so its dual bound cannot cut it off** | added here | **all 240 × 2** | all | all |
| MIP: no exactly feasible incumbent beats a proven optimum | added here | 0 | 0 | 0 |

The MIPLIB engine checks the optimum only when a solve finishes. The bracket rule extends the check
to the two thirds of the set that stop at the limit. A stopped run's dual bound must not cut off the
known optimum, and its incumbent must not beat it, under the same tolerance. That makes all 480 MIP
results falsifiable, not only the 150 or so that finish.

Reported, not asserted:

- **binkar10_1's published optimum is a tolerance artifact.** `.solu` v36–v37 lists 6741.38, taken
  from MIPLIB's solution 3 (2025). That solution needs 8.2e-7 of integrality slack. HiGHS and SCIP
  both prove 6742.200024 optimal, on every chip. MIPLIB's own solution 2 reaches that value with no
  violation, and the instance page lists the exact objective as 6742.2. The task runs all three
  published solutions through the checker. It then compares binkar10_1 against the best exactly
  feasible one, and prints the substitution. Versions v2–v35 listed 6742.1998835.
- **SCIP's incumbent on mas74 beats the proven optimum by 7.4e-6 relative** (11801.0985 against
  11801.18572). It does so through a row violation of 8.1e-7, inside SCIP's feasibility tolerance.
  This happens on all three chips, in a run stopped by the time limit. A solution with zero
  violation could not do it, and that case would fail the run.
- **HiGHS overruns its time limit at the root node on a few instances.** On germanrr, at a 300 s
  limit, the log has a root separation round at 38.7 s and its next line at 619 s. It returned at
  619 s on Graviton4, and neos-5049753-cuanza did the same. On both x86 chips germanrr was killed
  by the driver's guard at 675 s, as were neos-5049753-cuanza on Intel and buildingenergy on AMD. These are recorded as errors and
  counted. They are reported, not failed, because they are a time-limit defect, not a wrong answer.
- **E226's MPS carries an objective constant of 7.113** (an RHS on the objective row). All three
  solvers include it in the objective, and netlib's and SoPlex's published values exclude it. The
  check subtracts the constant HiGHS read from the file before comparing.
- **netlib's own 1989 readme is off beyond its printed digits for 18 of the 90 problems.** The worst
  is GREENBEA, at 1.3e-3 relative, and the readme itself notes the disagreement. That is why the
  exact SoPlex values are the reference here.
- **CBC's "After Postsolve … infeasibilities" line is not always the certificate.** On 9 of the LPs
  (80BAU3B, PILOT87 and others) it reports primal or dual infeasibility as large as 37, followed by
  "full model needs cleaning up". Clp then re-optimises. The certificate is the state before the
  last "Optimal": the clean-up's final iterate, which shows no infeasibility.

## Across chips: what is a fixed point and what is a path

**Every optimum agrees across chips.** An LP optimum is a fixed point, and so is a MIP optimum
proven at gap 0. Against Graviton4, both x86 chips:

- **Netlib LP.** SCIP's objectives are bit-identical on all 91 instances. HiGHS agrees to 9.4e-12
  relative, and is bit-identical on 43 of 91. CBC is bit-identical on 90 of 91. The exception is
  PILOT87, where arm64 prints 301.710365 and x86 prints 301.7103473, a relative difference of 5.9e-8.
  The x86 value is the one closer to the exact optimum.
- **MIPLIB, both chips finished.** SCIP's objectives are bit-identical on every instance. HiGHS
  agrees within 2.9e-10 (neos5, where both values are 15 within tolerance).

**The search is a path, and the two solvers differ in it:**

- **SCIP's search is the same on all three chips.** Its node count is identical on every instance
  that both chips finish (70 of 70 against AMD). Its runs differ only in speed.
- **HiGHS's search differs between arm64 and x86.** Its node counts match on only 25 of the 77
  instances that both Graviton4 and AMD finish. Intel and AMD agree exactly with each other on every
  instance: the same linux-64 build, objectives and node counts identical.
- **Which instances finish inside 300 s is a path quantity.** It moves with chip speed and, for
  HiGHS, with the search path. HiGHS on Graviton4 finishes 30n20b8 and neos-3216931-puriri, which
  it does not finish on AMD. On AMD it finishes 8 that it does not finish on Graviton4. All of it is in
  [`results/compare.txt`](results/compare.txt).

HiGHS and SCIP do not call BLAS, so OpenBLAS's kernel choice cannot be the cause. It is
`neoversev2` on Graviton4 and `Cooperlake` on both x86 chips. The arm64/x86 split is in the builds:
the conda-forge linux-aarch64 HiGHS against the linux-64 one. SCIP shows no such split, and its LP
solver is SoPlex.

## Caveats

- One time limit, 300 s, single-threaded. That is a twelfth of MIPLIB's default limit, and HiGHS's
  and SCIP's parallel modes were not run. The finished counts are not comparable to published
  1-hour or 8-thread results.
- 64 concurrent solves share a box's memory bandwidth and caches, which a solo run does not. The
  times are the box's throughput, not per-instance solver speed.
- CBC ran only on Netlib. The MIP checks are HiGHS and SCIP.
- The solution checker is the MIPLIB `solchecker` rule in float64, not its exact rational
  arithmetic. The float64 error is about 1e-16 of the same magnitudes, twelve orders below the
  1e-4 tolerance.
- Prices are on-demand us-west-2, as spawn reported them for these instances. The runs used a
  `cost_limit` of 4.0, scaled ×1.3 for Intel and ×1.6 for AMD by `spec_arch.py`. The spec is
  retightened from these runs to 3.0. Its TTL is a 3 h backstop.

## Spend

Twelve launches, about **$16.93** in all. The three verifying runs cost $7.20 billed. The rest was two
fetches, two small wiring runs, a 60 s-limit sizing canary, and four full runs. Of those four, two
were repeats that showed the runs are deterministic. One stopped on a bracket check that was
counting a tolerance-feasible incumbent as a wrong answer. One hit a work-file collision between
concurrent checks of binkar10_1's published solutions.
