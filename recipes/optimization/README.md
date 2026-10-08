---
tool: highs
tool_version: "1.15.1"
env: optimization
image: quay.io/aarchsci/optimization@sha256:460d6029d7cf5f30aa45e41312754b8724c284a34d4bb864d4f18820bd8837d4
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# HiGHS / SCIP / CBC — LP strong duality, and three solvers on one netlib instance

Solves a classic Netlib LP on Graviton4 with three unrelated solvers, checks each one's primal–dual certificate, and compares all three to the optimum netlib published in 1985. For anyone doing optimization or OR on ARM.

## Run it

```bash
make stage RECIPE=optimization     # once: afiro.mps, 3,271 bytes
spawn task run --spec "$(make -s spec RECIPE=optimization)" --wait
make ls RECIPE=optimization

cbc afiro.mps solve                               # -464.7531429
python3 -c "import highspy; h=highspy.Highs(); h.readModel('afiro.mps'); h.run()"
python3 -c "from pyscipopt import Model; m=Model(); m.readProblem('afiro.mps'); m.optimize()"
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `afiro.mps` | your `.mps` or `.lp` | all three read MPS; HiGHS and SCIP also read `.lp`. **Netlib's own files are *not* MPS** — see below. |
| the three solvers | one of them | **pick by licence and problem class:** HiGHS (MIT) and CBC (EPL) are open for any use; SCIP is free for academic use but **needs a commercial licence otherwise** — check before shipping it in a product. |
| LP | MIP (`integer` sections) | **strong duality does not hold for MIP** — there is a duality *gap*, so the central check here stops applying. Use the solver's reported bound gap instead. |
| the duality assertion | your own | **this is the part worth copying** — every LP solver reports a primal–dual certificate, so you can assert optimality from mathematics rather than trusting a status string. |

**Leave the fixture.** afiro is 28 rows × 32 columns and solves in 5 simplex iterations — the point is that its optimum has been *published* since 1985, which no larger instance here would give you. **Scale it** with `25fv47.mps` or any of the 82 MPS files in HiGHS's `check/instances/`; the duality check is size-independent, though only some have published reference values.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 20m, cap $0.05. All three solves finish in well under a second inside a **66 s** window that is almost entirely boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: three independent duality certificates, a 1985 published optimum, and why CBC's spread is printing not disagreement</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match the pin | `9cd304f0…` |
| MPS structure | `ROWS`/`COLUMNS`/`RHS`/`ENDATA` all present | all four |
| **HiGHS KKT** | **primal inf, dual inf, complementarity all ≈ 0** | **0.000e+00, 0.000e+00, 0.000e+00** |
| **SCIP duality gap** | **primal bound == dual bound** | **0.000e+00** |
| **CBC postsolve** | **dual and primal infeasibility ≈ 0** | **0.000e+00, 0.000e+00** |
| **vs netlib published** | all three within 1e-9 relative | **6.148e-12 / 6.147e-12 / 8.607e-11** |
| **three-solver spread** | < 1e-9 relative | **4.286e-08** absolute |

**Strong duality is the identity here, and each solver certifies it in its own currency.** For a
linear program the primal and dual optima coincide — that is a theorem, not an observation — so the
check is that the gap *vanishes*, with no tolerance to choose. SCIP reports both bounds explicitly;
HiGHS reports KKT residuals (primal feasible, dual feasible, complementary slackness); CBC reports
post-postsolve infeasibilities. All three are zero to the printed precision.

Reading each solver's **native** certificate matters. Reconstructing the dual objective as `bᵀy` by
hand is where a quietly-wrong metric would creep in — general MPS carries ranges and bounds whose
dual contributions are easy to drop, and the resulting gap would look like a solver defect.

### The residual against netlib is netlib's rounding, not solver error

netlib publishes `-4.6475314286E+02` — **11 significant figures**. HiGHS and SCIP both return
`-464.7531428571`, i.e. the double-precision optimum. The 6.1e-12 relative difference is the
*published value's own rounding*, not a discrepancy:

```text
true optimum (double)   -464.7531428571...
netlib published        -464.75314286        <- rounded to 11 figures
relative difference      6.1e-12             <- exactly the rounding
```

So the right statement is **HiGHS and SCIP reproduce the published optimum to the full precision at
which it was published.** A tolerance tighter than ~1e-11 would be asserting more precision than
netlib printed.

### CBC's 4.29e-08 spread is printing precision, not disagreement

CBC has no Python binding here, so its objective is parsed from stdout — and it prints the value
three times at **different precisions**:

```text
Optimal - objective value -464.75314                      8 figs  -> 6.2e-09 from published
After Postsolve, objective -464.75314, infeasibilities …   8 figs
Optimal objective -464.7531429 - 5 iterations            10 figs  -> 9.2e-11 from published
```

Parse the 10-figure line. The task records which source it used (`cbc_precision_source`) so the
number can never be silently coarser than the comparison assumes, and the `solver_spread` of
4.29e-08 is entirely CBC's last printed digit rather than a real disagreement.

**The general shape:** when a tool reports one quantity at several precisions, the line you parse
silently sets your tolerance. Coarse line plus a loose band and precise line plus a tight band look
identical in a passing run — only one of them is checking anything.

CBC's log also carries its own duality certificate (`infeasibilities - dual 0 (0), primal 0 (0)`),
which is why all three solvers here certify optimality rather than two certifying and a third
merely agreeing on a number.

### Why three solvers can be compared at all

**An LP optimum is unique in value even when the optimal vertex is not.** Degenerate LPs have many
optimal bases, so the three solvers may well return different `x` — but `cᵀx` is forced. That is
what makes a cross-check across three unrelated codebases meaningful here, and it is *not*
transferable to problems whose answer is a path or a basis
([cross-checks](../../practices/cross-checks.md)).

### Pins

| | |
|---|---|
| instance | `afiro.mps`, 3,271 B, sha256 `9cd304f0…`, from `ERGO-Code/HiGHS` at tag `v1.11.0` |
| reference | `-4.6475314286E+02`, from [netlib's own readme](https://netlib.org/lp/data/readme) |
| image | `@sha256:460d6029…` — HiGHS 1.15.1, SCIP 10.0.3, CBC 2.10.13, highspy 1.15.1, pyscipopt 6.2.1 |

**netlib's own `afiro` file is not MPS.** `netlib.org/lp/data/afiro` is 794 bytes of netlib's
compressed **`emps`** format, with no `ROWS`/`COLUMNS`/`RHS`/`ENDATA` — using it means compiling
`emps.c`, which is build-tier machinery for a one-line recipe. The bytes therefore come from HiGHS's
test corpus at an immutable tag, while the *reference value* stays netlib's. Staging asserts all four
MPS sections exist so a future repin to a compressed variant fails loudly rather than producing a
confusing parse error on the box.

cosign-verified against `playgroundlogic/aarchsci`; signature covers the **manifest-list** digest, so
verify the tag and pin the arm64 digest. Staleness checked too: the env lock reports
`Built: 2026.10.08.024733` and the tag was pushed 02:52:42 the same day — same build, the check that
[gsw](../gsw/README.md) exists because of.

`highspy.__version__` is absent in this build and reports `unknown` through a `getattr` fallback;
the version above is from the env lock. The fallback is deliberate — a line describing the run must
not be able to fail it.

### Run + verify

```sh
make stage RECIPE=optimization
spawn task run --spec "$(make -s spec RECIPE=optimization)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/optimization/r1/score.tsv" -
```

The checks run inside the task and fail it on a non-vanishing duality gap, any solver missing the
published optimum, or the three disagreeing — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

MIP (where strong duality does not hold), the other 81 instances in HiGHS's corpus, `ipopt` for
nonlinear problems (present in this env, unexercised), sensitivity and ranging analysis, and
anything needing a modelling layer — no `pyomo` or `pulp` in this env.

</details>
