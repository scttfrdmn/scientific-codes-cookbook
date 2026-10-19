# Z3 on three whole SMT-LIB logics (4,536 benchmarks) — Graviton4, Intel and AMD

> **Z3 never contradicts SMT-LIB's published status, and every one of its sat models satisfies the
> original file when an evaluator that shares no code with Z3 checks it.** That held on 3,387–3,461
> published verdicts and 2,072–2,158 models per chip. The three chips never disagree on a verdict.
> AMD EPYC decides the most benchmarks inside the 60 s limit (3,560), Intel the fewest (3,419).
> Graviton4 is the cheapest per decided benchmark.

Workload: **QF_IDL, QF_RDL and QF_LRA, whole**, from the SMT-LIB 2025 non-incremental release
([Zenodo 16740866](https://doi.org/10.5281/zenodo.16740866), version 2025.08.04). These are integer
difference logic, real difference logic and linear real arithmetic: 2,528 + 255 + 1,753 benchmarks,
6.8 GB uncompressed. They come from scheduling, timed automata, bounded model checking, termination
analysis (LassoRanker) and ASP encodings. This is the library SMT solvers are developed against and
SMT-COMP runs on. A benchmark's `:status` (sat / unsat / unknown) is the library's published truth:
2,526 are sat, 1,713 unsat and 297 unknown.

**Data tier: a durable public id.** [`00-fetch.task.json`](00-fetch.task.json) downloads the three
archives on AWS and checks each against the md5 Zenodo publishes for it. It then writes a sha256 of
each, which [`pins.sha256`](pins.sha256) holds and every stage-in checks again.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/z3-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if not staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py z3 $a "$B" "$(date +%s)" \
    measurements/z3-real/real.task.json)" --wait
done
bash measurements/z3-real/collect.sh "$B" <scratch dir>   # results/, results/compare.txt
```

[`driver.py`](driver.py) runs 64 single-threaded Z3 processes at a time, largest file first.
[`solve.py`](solve.py) is one benchmark. [`smtcheck.py`](smtcheck.py) is the independent evaluator,
and [`summarize.py`](summarize.py) holds the checks. The solver call is the plain one:

```python
s = z3.Solver(); s.set("timeout", 60_000); s.from_file(f); r = s.check()
m = s.model(); m.eval(v, model_completion=True)      # every declared constant, into smtcheck.py
```

## Results

`m8g.16xlarge`, `m8i.16xlarge` and `m8a.16xlarge` (64 vCPU, 256 GiB), with 64 solves at a time.
Graviton4 runs the recipe's image, and the two x86 chips run the `optimization` env's linux-64 twin.
All three use the same staged bytes. Z3 is 5.1.0 on all three.

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result | decided in 60 s | billed $ / decided |
|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.16xlarge` | 2,373 s | 2,427 s | 94.3 GiB | 2.872 | 1.893 | 1.936 | 3,475 | **0.000557** |
| Xeon 6975P-C | `m8i.16xlarge` | 2,388 s | 2,438 s | 81.6 GiB | 3.387 | 2.247 | 2.294 | 3,419 | 0.000671 |
| EPYC 9R45 | `m8a.16xlarge` | **1,963 s** | 2,006 s | 93.0 GiB | 3.895 | 2.124 | 2.170 | **3,560** | 0.000610 |

A result is the whole verified run: 4,536 solves, plus every model check and unsat-core check
below. Peak RSS is the box's anonymous memory with 64 solves running. The largest single process
was 3.3 GiB, under Z3's `memory_max_size` of 3,500 MiB. $/hr is spawn's cost estimate divided by
the billed window.

- **The time limit sets the wall clock, not the chip.** About 1,000 benchmarks per chip run out the
  60 s limit. The chip's speed shows up in how many benchmarks it decides inside the limit, so
  billed $ per decided benchmark is the column that ranks them.
- **On the benchmarks both chips decide, AMD needs 0.61–0.69× of Graviton4's summed solve time.**
  Intel needs 1.17–1.31×. Intel's 64 vCPU are 32 cores × 2 threads, so two solves share each core.
  AMD's and Graviton4's 64 vCPU are 64 full cores.
- **Graviton4 is the cheapest per decided benchmark.** AMD decides 85 more (+2.4%) for an hour that
  costs 36% more. Intel decides 56 fewer for 18% more.
- **m8g is the right family.** Peak anonymous memory was 82–94 GiB, plus 7 GiB of tmpfs. That is too
  close to a `c8g.16xlarge`'s 128 GiB, and an `r8g`'s extra memory would sit idle.
- **n = 1 per chip.** Z3 is deterministic on 3,408 of the 3,418 benchmarks both Graviton4 and Intel
  decide: same verdict, same `rlimit count`, same conflict count. The 10 exceptions are below.

## What held at scale, on all three chips

Every row is asserted inside the task by [`summarize.py`](summarize.py). A failure fails the run.

| claim | rule | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| every benchmark ran, once | count == the release README's | 4,536 | 4,536 | 4,536 |
| **no verdict contradicts the published `:status`** | SMT-LIB's own labels | **3,387 agree, 0 wrong** | 3,333, 0 | 3,461, 0 |
| **every sat model satisfies the file** | each `assert` evaluated by `smtcheck.py`, exact rationals | **2,110 / 2,110** | 2,072 / 2,072 | 2,158 / 2,158 |
| every unsat core is unsat on its own | fresh solver, core only | 1,176 (47 ran out of time) | 1,162 | 1,229 |
| **each core member is necessary** | drop one: the rest is sat, and `smtcheck.py` confirms the model | **2,386 members, 0 redundant** | 2,080, 0 | 2,926, 0 |
| control: that model breaks the whole file | the file is unsat, so the evaluator must reject it | 2,386 / 2,386 rejected | 2,080 / 2,080 | 2,926 / 2,926 |

A sat verdict is checked by evaluating Z3's own model against the benchmark text, in Python `int`
and `Fraction`, with its own s-expression reader. So a wrong model fails whatever Z3 believes. An
unsat verdict cannot be evaluated, so it is checked against the published status. The core checks
add a second, Z3-independent argument wherever a file has more than one assertion. The control
row shows the evaluator can say no: on every unsat file, a model that satisfies all but one core
member must break some assertion of the full file, and it did, every time.

Reported, not asserted:

- **Most unsat benchmarks are a single `assert`.** 1,259 of the 1,365 Graviton4 unsat verdicts come
  from files with one top-level assertion, so their core is that assertion and the necessity check
  is trivial. On the 106 multi-assertion files, 60 cores were found and re-solved in time. 59 of
  those 60 are smaller than the file (the largest core has 774 assertions), and 50 were shown minimal
  member by member before the time ran out.
- **Z3 decides 88 benchmarks the release labels `unknown`** (67 sat, 21 unsat, on Graviton4). The sat
  ones are model-checked like the rest. The 21 unsat ones have no published truth, but all three
  chips agree on them.
- **Z3 overruns its own timeout on 30–32 benchmarks per chip.** The driver kills a process at 720 s
  (twice the limit plus 600 s). The vlsat3 `i` series in QF_IDL and the `ecoliMILP` files in QF_LRA
  run that long against a 60 s `timeout`. On 2 LassoRanker files per chip Z3 aborts with an
  uncaught `out_of_memory_error` (SIGABRT) instead of returning `unknown`. Both are counted as not
  decided. They are reported, not failed, because they are a resource-limit defect, not a wrong
  answer.

## Across chips: what is a fixed point and what is a path

**Every verdict agrees across chips.** A sat/unsat verdict is a fixed point, and there are 0
disagreements on the 3,418–3,475 benchmarks each pair both decide. That includes the 21 unsat
verdicts the release has no label for. Full detail is in
[`results/compare.txt`](results/compare.txt).

**Z3's search is the same on every chip except in two families.** `rlimit count` is Z3's
deterministic work counter, and it is identical on every decided benchmark except 10: all 7
decided `bcnscheduling` files and all 3 decided `vlsat3` files in QF_IDL. Those 10 differ
between every pair of chips, **including Intel against AMD, which run the same linux-64 binary**.
So the cause is not the arm64 build, and it is not run length either: between Intel and AMD, 117
other benchmarks that take over 19 s on both are identical. Something those two families exercise depends on the host. It was
not traced further.

**The core is a path.** On a few benchmarks the minimised core differs in size across chips with
the same `rlimit`, for example `blending/16.smt2` (110 members on Graviton4 and AMD, 219 on Intel).
None of those cores is sat on its own. A minimal unsat core is not unique, and minimisation
is time-bounded.

**Which benchmarks finish inside 60 s is a path quantity.** It moves with chip speed. Graviton4
decides 31 QF_IDL benchmarks that Intel does not, and AMD decides everything Graviton4 does plus 85
more.

Z3 does not call BLAS, so OpenBLAS's kernel choice is not a factor here.

## Caveats

- One time limit, 60 s, single-threaded. SMT-COMP uses 1,200 s. The decided counts are not
  comparable to published competition results, and the unsat-core step gets its own 60 s on top.
- 64 concurrent solves share a box's memory bandwidth and caches, which a solo run does not. The
  times are the box's throughput, not per-benchmark solver speed.
- `smtcheck.py` covers the operators these three logics use. A file using anything else would be
  reported "not checked". None was: all 2,110 / 2,072 / 2,158 models were evaluated.
- Prices are on-demand us-west-2, from spawn's cost estimate for these instances. The spec's
  `cost_limit` is 3.0, scaled ×1.3 for Intel and ×1.6 for AMD by `spec_arch.py`. Its TTL is a 3 h
  backstop.

## Spend

Six launches, about **$6.57** in all. The three verifying runs cost $6.40 billed. The rest was the
fetch, a canary that found no benchmarks (the archives unpack under `non-incremental/`), and a
98-benchmark canary on `m8g.4xlarge` that sized the box.
