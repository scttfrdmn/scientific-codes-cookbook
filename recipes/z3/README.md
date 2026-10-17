---
tool: z3
tool_version: "5.1.0"
env: optimization
image: quay.io/aarchsci/optimization@sha256:29abbb67fb16c16292ec38ccbc65c099e722935fae02cb7fccc77c14febb7f86
env_x86_64: optimization-f8c54d155671
spawn_version: 0.127.0
last_verified: 2026-10-16
last_verified_x86_64: 2026-10-16
---
# Z3 — SAT, SMT, bit-vectors and Optimize, every verdict checked outside the solver

Runs Z3's Python API on Graviton4 across Boolean, integer, bit-vector and optimization problems, and checks each answer independently of Z3. For anyone doing verification, constraint solving or program analysis on ARM.

## Run it

```bash
make stage RECIPE=z3     # once: the checks only — every problem is built in the script
spawn task run --spec "$(make -s spec RECIPE=z3)" --wait
make ls RECIPE=z3

s = z3.Solver(); s.add(constraints); s.check()            # sat / unsat
m = s.model(); m.eval(v, model_completion=True)            # read EVERY variable, constrained or not
s.assert_and_track(c, z3.Bool("c1")); s.unsat_core()       # which assertions made it unsat
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the built-in problems | your constraints | **check a `sat` model yourself.** `m.eval(...)` back into plain Python costs nothing and is the only evidence the answer is right, not just returned. |
| `m.eval(v, model_completion=True)` | `m[v]` | `m[v]` is `None` for a variable the solver never had to fix — fine until one of yours is unconstrained. |
| `BitVec` `/`, `<`, `>=` | `UDiv`, `ULT`, `UGE` | **the Python operators are *signed*** on bit-vectors. The midpoint overflow here is found only because `>=` and `/` are signed; for unsigned code use the `U*` functions. |
| blocking clauses to enumerate | `z3.Optimize` | enumeration (N-queens) grows with the solution count; Optimize finds one optimum. For a large pure IP, [HiGHS](../optimization/README.md) in the same image is the dedicated MIP solver — Z3 earns its place with mixed theories. |

**Leave the fixtures.** Each one has a known answer — a theorem, a published count, a brute force — which is what makes the verdict checkable; larger instances would only be slower. **Scale it** by swapping in your own constraints and keeping the model-evaluation step.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 30m as a **backstop** with `cost_limit` $0.05 as the real guard. The checks take **3.0 s** of a ~77 s window that is mostly boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: pigeonhole cores of exactly n+1, 92 queens, a unique Sudoku, a 32-bit identity proven, and an integer optimum equal to brute force and HiGHS</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| z3 / python / highspy | read from the running install | **5.1.0** / 3.14.8 / 1.15.1 |
| **pigeonhole n+1 → n, n = 2..6** | **unsat; the core is all n+1 "pigeon sits" clauses** | **unsat, cores 3/3, 4/4, 5/5, 6/6, 7/7** |
| pigeonhole n → n | sat; the model places every pigeon, one per hole (Python) | 5 of 5 |
| **Petersen graph** (10 v, 15 e) | **2 colours unsat, 3 sat**; colouring checked on every edge | as asserted |
| **Grötzsch graph** (11 v, 20 e, triangle-free) | **3 colours unsat, 4 sat**; colouring checked on every edge | as asserted |
| **N-queens, N = 1..8, by blocking clauses** | **== OEIS A000170**, every solution valid and distinct; Python backtracking gives the same | **1, 0, 0, 2, 10, 4, 40, 92** both ways |
| **Sudoku (Inkala 2012, 21 clues)** | **== the published solution; blocking it is unsat** | equal, unique |
| control: the Python verifiers | reject a diagonal-clash queens placement and a swapped-pair grid | rejected |
| **`x+y == (x^y)+2(x&y)`, `x^y == (x\|y)-(x&y)`** | **negation unsat at 32 and 64 bits**; exhaustive in Python at 8 bits | unsat; 65,536 pairs each |
| **`x&(x-1)==0` ⇔ power of two** (false) | **sat; counterexample breaks it in Python; excluding it is unsat** | **x = 0**, the only one |
| **`(lo+hi)/2` vs `lo+(hi-lo)/2`, signed 32-bit** | **sat; reproduces in 32-bit Python, naive out of `[lo,hi]`** | lo=3033049, hi=2146422769 → −1072755739 vs 1074727909 |
| **0-1 knapsack, 15 items, 2 capacities** | **Z3 optimum == brute force (32,768 subsets) == HiGHS MIP** | **342 / 342 / 342** |
| **bounded IP, 4 vars, 3 rows** | **Z3 optimum == brute force (3,780 points) == HiGHS MIP** | **−43 / −43 / −43** |

### Why these are the checks

A `sat` is checked by evaluating Z3's model in plain Python against the original constraints,
so the answer is right whatever happened inside the solver. An `unsat` can't be evaluated, so each
one is either a theorem (pigeonhole; a 3-chromatic and a 4-chromatic graph, the second built here
by Mycielski's construction from C5) or is backed by exhaustive search (the bit-vector identities
at 8 bits, the N-queens counts, both optima). The pigeonhole core is exact because the problem
fixes it: any n pigeons fit in n holes, so no proper subset of the n+1 clauses is unsat.

"Exactly one counterexample" and "exactly 92 solutions" are claims about **what is absent**, so
they are proven the same way: block what was found, and the remainder must be unsat. The Optimize
checks are a [cross-check](../../practices/cross-checks.md) on identical instances — Z3's
optimization engine against HiGHS's branch-and-bound — and the tolerance is zero because an
integer program has an exact optimum.

### Pins

| | |
|---|---|
| data | **none staged** — every problem is constructed in `identities.py`; the Sudoku and A000170 are published, the rest are theorems or brute force |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/optimization@sha256:29abbb67fb16…` — z3-solver 5.1.0.0, highspy 1.15.1, pyscipopt, CBC, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`sha256:dc6ee5c0…`), so verify that and pin the arm64 entry. A newer digest than
[optimization](../optimization/README.md) pins — that recipe stays on the image it was verified
against.

### On x86_64

The same task on `c8i.large`, through the `optimization` env's linux-64 twin
(`envs-x86_64/optimization-f8c54d155671.linux-64.lock`: 63 packages, every versioned package
identical to arm64; the BLAS provider is BLIS on linux-64 where arm64 has OpenBLAS, which nothing
here calls). `score.tsv` is **identical** to arm64 line for line except `wall_s` (2.6 s vs 3.0 s):
every verdict, every core size, all eight N-queens counts, the same midpoint counterexample, and
both optima (342, −43) from Z3, brute force and HiGHS. Verdicts and integer optima are exact, so
nothing here may differ.

### Run + verify

```sh
make stage RECIPE=z3
spawn task run --spec "$(make -s spec RECIPE=z3)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/z3/r1/score.tsv" -
```

Fails on a pin mismatch, any verdict other than the expected one, a model that breaks a
constraint when evaluated in Python, a pigeonhole core smaller than n+1, an N-queens count off
A000170, a Sudoku that differs from its published solution or is not unique, a bit-vector
counterexample that does not reproduce, a second power-of-two counterexample, or an optimum that
differs from brute force or HiGHS. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Z3's own regression suite** (`Z3Prover/z3test`) is not in the package and not staged.
**Real arithmetic, strings, arrays, quantifiers and the SMT-LIB2 front end** (`z3 file.smt2`) are
not exercised; nor are tactics, timeouts, or hard instances where solver heuristics and seeds
start to matter.

</details>
