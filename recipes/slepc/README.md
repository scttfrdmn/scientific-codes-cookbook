---
tool: slepc
tool_version: "3.25.2"
env: fem-cfd
image: quay.io/aarchsci/fem-cfd@sha256:acf6b57d76a151adf351f3cb4bd811188db40967b42fef4985a5e307aa6ee718
env_x86_64: fem-cfd-1950e1068faa
spawn_version: 0.127.0
last_verified: 2026-10-12
last_verified_x86_64: 2026-10-17
---
# SLEPc — eigenvalues against a closed form, on 1 rank and on 2

Solves for the smallest eigenvalues of a 1-D Laplacian on Graviton4 and checks them against an exact formula, an independent dense solver, and the same run distributed across two ranks. For anyone doing eigenproblems on ARM.

## Run it

```bash
make stage RECIPE=slepc      # once: the checks only — the reference is a closed form
spawn task run --spec "$(make -s spec RECIPE=slepc)" --wait
make ls RECIPE=slepc

eps = SLEPc.EPS().create(comm=PETSc.COMM_WORLD)
eps.setProblemType(SLEPc.EPS.ProblemType.HEP)          # symmetric
eps.setWhichEigenpairs(SLEPc.EPS.Which.SMALLEST_REAL)
eps.solve()                   # lambda_k == 4 sin^2(k pi / 2(n+1)), exactly
A.getOwnershipRange()         # rows 0..100 of 200 — proves it is actually distributed
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `getOwnershipRange()` asserted | trusting `-n 2` | **keep it.** A nompi build under `mpiexec -n 2` gives the *right eigenvalues* from two independent serial solves. Owning 100 of 200 rows is what proves distribution; the rank count alone does not ([silent serial build](../../practices/mpi-rank-count.md)). |
| the `[-1,2,-1]` stencil | your operator | its eigenvalues are `4sin²(kπ/2(n+1))` **exactly**, so there is a reference for all 200 of them and nothing to stage. |
| scipy `eigh_tridiagonal` as referee | SLEPc alone | dense LAPACK versus Krylov-Schur — genuinely different code. Note scipy is itself **1.2e-12** from exact, so the two agree to within their own accuracies, not to an arbitrary bound. |
| n = 200, 6 eigenpairs | larger | 44 iterations and 9 s of compute. The smallest relative gap is 0.36, which staging checks — nearly degenerate eigenvalues would make "the k-th smallest" ambiguous. |
| `SMALLEST_REAL` | `LARGEST_*`, shift-invert | this env also carries PETSc's preconditioners and MUMPS-class direct solvers for spectral transformations, all unexercised here. |

**Leave the fixture.** Nothing is fetched: the operator is a stencil and the reference is mathematics. **Scale it** by raising `n` — the closed form holds at any size, and staging re-verifies it against a dense diagonalisation whenever you change it.

## Shape, size, cost

One task on `c8g.large` (4 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. Both passes — serial then 2-rank — are **9 s** of a 1m12s window; 24 s is pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a closed form verified independently of the solver, a dense-vs-Krylov cross-check, and the distribution asserted rather than assumed</summary>

### The checks

| observable | assertion | observed (Graviton4) | x86_64 (`c8i`) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | 1 of 1 |
| petsc / slepc / numpy | read from the running install | **3.25.6** / 3.25.2 / 2.5.4 | identical |
| scalar type | real | float64 | float64 |
| matrix symmetric | required for HEP | yes | yes |
| converged | ≥ 6 requested | 7, in 44 iterations | 7, in 44 (42 on 2 ranks, both chips) |
| **eigenvalues vs `4sin²(kπ/2(n+1))`** | **< 1e-11 relative** | **5.166e-13** serial, **3.477e-13** on 2 ranks | **1.913e-12** serial, **2.077e-13** on 2 ranks |
| worst reported residual | < 1e-9 | 1.039e-11 | 1.230e-11 |
| ordering | strictly increasing, matched k-for-k | yes | yes |
| control: `[-1,3,-1]` operator | must not match | **9.913e-01** | **9.913e-01** |
| scipy vs closed form | *recorded* | 1.227e-12 | 3.935e-13 |
| **SLEPc vs scipy** | **< 1e-10** | **1.422e-12** | **2.307e-12** |
| **eigenvector orthonormality** | **`XXᵀ = I` < 1e-12** | **2.554e-15** | **7.550e-15** |
| **MPI ranks** | **== 2, read from inside** | **2** | **2** |
| **rank 0 ownership** | **strict subset of the rows** | **rows 0..100 of 200** | **rows 0..100 of 200** |
| **serial vs 2-rank spectrum** | **< 1e-11 relative** | **7.428e-13** | **1.903e-12** |

**x86_64** runs the same spec on the env's linux-64 twin (`envs-x86_64/fem-cfd-1950e1068faa`,
installed offline, all 131 versions identical to arm64, including the openmpi build of PETSc). The
rank count, the ownership split and the iteration counts are identical. The eigenvalues are a
converged fixed point and agree with the closed form on both chips; the last few digits move, as
does scipy's dense LAPACK, because the OpenBLAS kernels differ by host. All of it stays inside the
bounds, which are set by the requested tolerance and not by either chip.

### A reference that is mathematics, verified without the solver

The tridiagonal `[-1, 2, -1]` matrix has eigenvalues `λ_k = 4 sin²(kπ / 2(n+1))` exactly. SLEPc
lands within **5.2e-13** of the six smallest, against a requested tolerance of 1e-12 — so the
bound is set by what was asked for, not by what came back.

**Staging validates the formula rather than the solver**, which is the part that would otherwise
be circular: it builds the matrix densely, diagonalises it with `numpy.linalg.eigvalsh`, and
checks the closed form reproduces all 200 eigenvalues (worst **1.227e-12**). A mis-derived formula
would be caught there, before any SLEPc number was compared against a wrong reference.

Two more things staging asserts, because the in-run checks depend on them: the smallest relative
gap among the low eigenvalues is **0.3608** (nearly degenerate values would make "the k-th
smallest" ambiguous and let a mis-ordered solver pass), and the control operator `[-1,3,-1]` sits
**113.8** relative away — comfortably distinguishable.

### Dense LAPACK as a second opinion, with its own error acknowledged

```text
scipy eigh_tridiagonal vs closed form    1.227e-12
SLEPc                  vs closed form    5.166e-13
SLEPc                  vs scipy          1.422e-12
```

scipy's dense LAPACK path shares no code with SLEPc's Krylov-Schur iteration, so this is a real
cross-check. The honest framing is in the third line: SLEPc and scipy differ by about as much as
**scipy itself differs from exact**, so they agree to within their own accuracies. Asserting a
bound tighter than scipy's own error would be claiming more than either tool delivers — the same
reasoning that set the PySCF/Psi4 tolerance from the integral treatment rather than from the
observed gap.

Eigenvector orthonormality is separately exact to **2.554e-15**: for a symmetric problem the
eigenvectors must form an orthonormal set, which no reference value is needed to state.

### The distribution is asserted, not inferred from the launch

This is the check that catches a *silent* failure rather than a wrong number. A nompi build run
under `mpiexec -n 2` produces perfectly correct eigenvalues — from two independent serial solves —
and the only symptom is a false claim about parallelism. Reading the rank count catches some of
that; reading the **ownership range** catches the rest:

```text
serial        mpi_ranks 1    rank 0 owns rows 0..200 of 200
mpiexec -n 2  mpi_ranks 2    rank 0 owns rows 0..100 of 200   <- genuinely partitioned
```

The run fails if a rank under `-n 2` owns all 200 rows, with the message that this is a serial
build running N copies. Staging additionally asserts the `getOwnershipRange()` check is still
present in the script, because deleting it restores the silent pass.

**And the two passes are compared against each other, not merely against the formula.** The serial
pass writes its eigenvalues to `serial.json`; the 2-rank pass reads them back and asserts
agreement to **7.428e-13**. Both agreeing with the closed form independently would be weaker —
this way the same operator is shown to give the same spectrum however it is distributed.

### Pins

| | |
|---|---|
| data | **none.** A tridiagonal stencil whose spectrum is a closed form, verified in staging against a dense diagonalisation |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/fem-cfd@sha256:acf6b57d76a151adf3…` — slepc 3.25.2 / slepc4py, petsc 3.25.6 / petsc4py (real, openmpi), fenics-dolfinx 0.11.0, mpi4py 4.1.2, scipy 1.18.1, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

This gap was named by the catalog itself — [petsc](../petsc/README.md)'s own page records
"SLEPc eigenproblems (`slepc4py` is in this env, unexercised)".

`OMPI_ALLOW_RUN_AS_ROOT` is set in the spec because the container runs as root and OpenMPI
refuses by default; without it `mpiexec` aborts before SLEPc is reached.

### Run + verify

```sh
make stage RECIPE=slepc
spawn task run --spec "$(make -s spec RECIPE=slepc)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/slepc/r1/score.tsv" -
```

Fails on a pin mismatch, an asymmetric operator, fewer than 6 converged pairs, an eigenvalue more
than 1e-11 from the closed form, a residual above 1e-9, a non-increasing spectrum, a control
operator that matches, a scipy disagreement beyond 1e-10, eigenvectors that are not orthonormal,
a rank count other than 2 in the parallel pass, a rank that owns every row, or a serial/parallel
spectrum mismatch — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)). Both logs are staged out, so a failure
in either pass is diagnosable without a rerun.

### Not covered

**Spectral transformations.** No shift-invert, no `ST` configuration, no targeting an interior
eigenvalue — which is what SLEPc is usually reached for on real problems, and where the
preconditioners in this env would matter.

**Generalised and non-symmetric problems** (`GHEP`, `GNHEP`, quadratic eigenproblems), so nothing
here exercises complex spectra or the balancing that needs. **Larger rank counts** — 2 ranks
proves distribution but says nothing about scaling. **SVD and polynomial eigensolvers**, both in
SLEPc and untouched. **A FEM operator**: the matrix is a hand-built stencil, not assembled by
`fenics-dolfinx`, which is in this env and would make the eigenproblem a physical one —
[fenicsx](../fenicsx/README.md) covers that solver separately.

</details>
