---
tool: petsc
tool_version: "3.25.5"
env: fem-cfd
image: quay.io/aarchsci/fem-cfd@sha256:6cadf382f817fb3967b0bda58c561114dce1ae3176fd7bbf25c5e70d56392e79
spawn_version: 0.123.0
last_verified: 2026-10-09
---
# PETSc — reproducing its own committed KSP output, serial and on 2 ranks

Solves PETSc's `ex2` Poisson problem on Graviton4 and reproduces the iteration count and error norm committed in its repository, then measures the discretisation order on a manufactured solution. For anyone running sparse solvers on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=petsc)" --wait   # nothing to stage
make ls RECIPE=petsc

# the committed reference, src/ksp/ksp/tutorials/output/ex2_1.out:
#   Norm of error 0.000392701 iterations 4
mpiexec -n 1 python3 ex2.py ex2     # 4 iterations, 0.000392700601
mpiexec -n 2 python3 ex2.py ex2     # 7 iterations, 0.000292348878
```

**Nothing is staged.** The problem is built in `petsc4py`; the reference values come from PETSc's repository at the version tag matching the env.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 5-point Laplacian | your operator | `ex2` uses an **unscaled** stencil (−1 off, 4.0 on the diagonal) with `u=1` and `b=Au`, so the exact solution is known by construction. |
| `rtol = 1e-2/((m+1)(n+1))` | your tolerance | **ex2.c sets a grid-dependent rtol, not PETSc's default.** Reproducing its output needs its configuration, not just its mathematics — see below. |
| `gmres` + `ilu` | `cg`+`icc`, `hypre`, `mumps` | this env has hypre and slepc too. The default PC differs between serial (`ilu`) and parallel (`bjacobi`), which is why the committed answers differ. |
| `getConvergedReason()` | a recomputed residual | **this is the part worth copying** — GMRES converges on the *preconditioned* norm, so a residual you compute afterwards is a different quantity. |
| the order ladder | your own | assert the measured **order**, not an error ratio; the ratio depends on your refinement factor. |

**Leave the fixture.** A 5×5 grid is 25 unknowns and the point is that PETSc *publishes* what the answer should be, which no larger problem here would give you. **Scale it** once it passes — this env carries hypre, MUMPS-class direct solvers and SLEPc for eigenproblems.

## Shape, size, cost

One task on `c8g.xlarge` (4 vCPU / 8 GiB), TTL 30m, cap $0.10. Two `ex2` solves plus a four-rung refinement ladder finish in seconds; the recorded window is boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: two committed references, a rank-count guard, the solver's own verdict, and a measured order</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| petsc / petsc4py | 3.25.x — the reference is version-matched | **3.25.5 / 3.25.5** |
| **serial iterations** | **= 4, from `output/ex2_1.out`** | **4** |
| **serial error norm** | **= 0.000392701** | **0.000392700601** |
| **2-rank iterations** | **= 7, from `output/ex2_2.out`** | **7** |
| **2-rank error norm** | **= 0.000292349** | **0.000292348878** |
| **MPI rank count** | **asserted from inside the run** | 1 and **2** |
| **converged reason** | **> 0 (PETSc's own verdict)** | **2 = KSP_CONVERGED_RTOL** |
| **convergence order** | **p ≈ 2 on every rung** | **1.992, 1.998, 1.999** |

### Reproducing the committed output needs its *configuration*

`ex2.c` line 164 sets a **grid-dependent** tolerance, not PETSc's default:

```c
KSPSetTolerances(ksp, 1.e-2 / ((m + 1) * (n + 1)), 1.e-50, PETSC_CURRENT, PETSC_CURRENT);
```

For `m=n=5` that is `1e-2/36 = 2.78e-4`, about 28× looser than the 1e-5 default. The arithmetic
predicts the committed iteration count exactly: the threshold is `2.78e-4 × 3.21109 = 8.92e-4`,
iteration 3 sits at 7.88e-3 and iteration 4 at 3.87e-4, so GMRES stops at **4**.

Using the default instead converges *further* — 5 iterations and a smaller error — which reads as
a disagreement with the reference and is really a configuration difference. Worth noting the
direction: being **more** converged than the reference points at a tolerance, where a wrong matrix
or preconditioner would diverge or stall.

### Serial and parallel legitimately differ, so each is checked against its own reference

The default preconditioner is `ilu` on one rank and `bjacobi` on two, so the two runs take
different Krylov paths and PETSc commits **different** expected outputs for them. Asserting
serial == parallel would be wrong here; reproducing each against its own committed file is both
stronger and correct.

**The rank count is asserted from inside the run**, which matters concretely: conda-forge ships
`nompi` PETSc builds at *higher build numbers* than the mpi ones, so an unpinned solve can hand
back a serial binary that under `mpiexec -n 2` runs two independent rank-0 problems — both
printing the same answer while a naive parallel-equals-serial check passes vacuously
([rank-count guard](../../practices/mpi-rank-count.md)).

### The solver's verdict, not a residual recomputed afterwards

GMRES converges on the **preconditioned** residual by default. An unpreconditioned
`‖b−Ax‖/‖b‖` computed after the solve is a *different quantity* and can legitimately exceed
`rtol` — measured **3.578e-04** against `rtol` 2.78e-04 on a run that converged correctly.

So the assertion is `KSPGetConvergedReason() > 0` — PETSc stating that its own criterion was met,
which is a completion sentinel rather than a band. The unpreconditioned residual is reported
alongside, not asserted.

### Assert the order, not the error ratio

The manufactured solution is `u = sin(πx)sin(πy)`, which is **not** representable in the discrete
space, so the 5-point stencil's error must fall as O(h²). The ladder runs with `rtol 1e-13` so
solver error cannot contaminate discretisation error.

**An error *ratio* depends on the refinement factor, not only on the method.** A 1.5× refinement
gives `1.5² = 2.25` for a second-order scheme, which a band centred on 4 rejects as a failure —
and that is exactly what happened on an N=64→96 rung measuring 2.226. The measured order

```text
p = log(e₁/e₂) / log(h₁/h₂)
```

is the theoretical quantity and is invariant to how the ladder is spaced:

```text
h 0.05882 -> 0.03030   p 1.992
h 0.03030 -> 0.01538   p 1.998
h 0.01538 -> 0.01031   p 1.999      <- a 1.49x refinement, same order
```

A correct-but-first-order implementation fails this while sitting comfortably inside any
single-resolution tolerance.

### Pins

| | |
|---|---|
| references | `petsc/petsc` at tag `v3.25.5`, `src/ksp/ksp/tutorials/output/ex2_{1,2}.out` |
| image | `quay.io/aarchsci/fem-cfd@sha256:6cadf382…` — petsc 3.25.5, petsc4py 3.25.5, openmpi 5.0.10 |

**Verified at both tags before relying on it:** `ex2_1.out` is byte-identical at `v3.25.5` and
`v3.26.0`, so the reference is stable across that step — established by checking rather than by
assuming.

The version is read from the **running binary**, not from the env lock, because this env's lock
reports `Built: 2026.10.08.014833` while the newest image tag was pushed ~13 h later. A lock file
in git and an image in a registry are two different artifacts.

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=petsc)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/petsc/r1/score.tsv" -
```

Fails on a version mismatch, either committed reference missing, a wrong rank count, a
non-convergence reason, or a measured order outside ~2 — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

SLEPc eigenproblems (`slepc4py` is in this env, unexercised), hypre and algebraic multigrid as
preconditioners, DMDA-structured grids, TS time stepping, SNES nonlinear solves, and scaling
beyond 2 ranks — the knee for a sparse solve is a different measurement from this correctness
check.

</details>
