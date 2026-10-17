---
tool: fenicsx
tool_version: 0.11.0
env: fem-cfd
image: quay.io/aarchsci/fem-cfd@sha256:941bdafc519347a600fa4c2805971a3beec8cce561f52b9152b8676277978c68
env_x86_64: fem-cfd-49dfa59a2aff
spawn_version: 0.126.1
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-16
---
# FEniCSx — a Poisson solve verified by patch test and convergence order

FEniCSx (`dolfinx`) solves the Poisson equation by finite elements and checks itself two ways a bug can't fake — a machine-zero patch test and the theoretical convergence rate. The catalog's first finite-element recipe, for anyone doing FEM who wants it on Graviton.

> **What this covers.** dolfinx 0.11 solving Poisson on the unit square (P2 Lagrange, method of manufactured solutions), over 2 MPI ranks — the open, runnable answer where commercial FEA/CFD (ANSYS, Abaqus, COMSOL) is license-gated and can't be. Not a benchmark; a small mesh, and the mathematics does the verifying.

## Run it

```bash
make stage RECIPE=fenicsx     # once: the checks only — the reference is arithmetic
spawn task run --spec "$(make -s spec RECIPE=fenicsx)" --wait
```
```python
import ufl
from dolfinx import fem, mesh
from dolfinx.fem.petsc import LinearProblem
domain = mesh.create_unit_square(comm, N, N)
V = fem.functionspace(domain, ("Lagrange", 2))          # P2 elements
u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
a = ufl.inner(ufl.grad(u), ufl.grad(v)) * ufl.dx        # weak form of -Δu = f
uh = LinearProblem(a, f*v*ufl.dx, bcs=[bc],
                   petsc_options={"ksp_type": "preonly", "pc_type": "lu"}).solve()
```

The recipe wraps this in the method of manufactured solutions: pick an exact `u`, set `f = -Δu`, solve, and measure the L2 error — for a polynomial `u` (patch test) and a transcendental one (convergence).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the manufactured Poisson problem (unit square) | your PDE + geometry | dolfinx expresses the weak form in UFL; swap the forms and the mesh — the MMS *verifies* whatever you solve. |
| P2 Lagrange elements | P1 / P3 / … | the convergence check asserts rate **p+1**, and the recipe now *demonstrates* it: P1 converges at **1.998**, P2 at **3.000**. P1 is also the patch test's control — it cannot represent a quadratic, so it fails that check by 6.8e11×. |
| direct LU solve | GMRES + AMG for large problems | LU is exact and right for this size; real problems scale with a Krylov solver + preconditioner — the [scaling knee](../../patterns/sizing.md). |

**Leave the fixture:** the manufactured solution is the point — it gives an exact answer to check against, which a real problem can't. **Scale it** to your physics; the two checks (exact reproduction, correct order) travel to any PDE.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the two vCPUs are the two MPI ranks), TTL 5m, cap $0.04. Measured on the box: **38 s** Docker install + **66 s** image pull (0.67 GB) dominate; the entire dolfinx exec — Python import, JIT form-compilation, and all five solves — was **6 s**. dolfinx JIT-compiles the variational forms at runtime (the env's gcc), which is the FEM timing wildcard in general, but for these simple Poisson forms it's cheap and lives inside that 6 s — the pull is the cost, as always. **These timings are not compute cost.** **Sizing:** the unit-square mesh is tiny; a real problem scales with degrees of freedom (mesh × element order) and moves to a Krylov solver across ranks, where the [scaling knee](../../patterns/sizing.md) lives. Size a real model on its DOF count.

<details>
<summary>As shipped: the two checks, why the rate not the error, pins, smoke check, run + verify</summary>

### Three checks a bug can't fake, and the control that proves the first

- **Patch test — exact** (the FEM analogue of BLAST's self-hit): a degree-*p* element space represents any polynomial of degree ≤ *p* exactly, so with a manufactured **quadratic** solution the P2 L2 error is machine zero — **1.07e-13**. No band, no tolerance; a wrong assembly, quadrature, or solve breaks it at once.
- **Convergence order — fixed by theory** (conservation-class): with a manufactured `sin(πx)sin(πy)` — *not* representable in the element space — the L2 error falls as h^(p+1). For P2 the rate is **3**, observed **3.00** at every step of an N = 8→16→32→64 refinement. The rate is set by the mathematics, not the fixture, so it is exact-or-wrong: a bug converges at the wrong order, or not at all. **Asserting the rate across a refinement sequence beats asserting the error at one resolution** — the latter needs a justified band, the former is theory-fixed. Same reason the lattice constant beat total energy for [Quantum ESPRESSO](../quantum-espresso/README.md).
- **MPI — rank count *and* the agreement it licenses**: dolfinx partitions the mesh across ranks; the recipe runs on 2 and asserts `MPI.COMM_WORLD.size == 2`, so a serial fallback can't masquerade as parallel ([rank-count guard](../../practices/mpi-rank-count.md)). It now also asserts the **answer is the same**: every discretisation error agrees between serial and 2 ranks to **2.434e-09** relative, 411× inside the bound. The rank count is what proves the parallelism being cross-validated actually happened; the agreement is the cross-validation.

**The control that makes the patch test mean something.** "P2 reproduces a quadratic exactly" is only informative if some element space *fails* to — otherwise it could be a property of the solver rather than of containment. So the same quadratic is solved in **P1**, which cannot represent it: **8.235098e-03**, or **6.8e11×** worse than P2's 1.21e-14. Measured, not argued. It also corrects a plausible misreading of the FEniCS tutorial, which runs this quadratic in P1 and reports ~1e-15 — that figure is a superconvergence property of the *vertex* values, not the L2 error.

On the Graviton verifying run the L2 errors and rates came back **bit-identical to local** (5.4806e-04 → 1.0753e-06, rates 3.00); only the patch-test residual moved a last digit (1.06e-13 vs 1.07e-13), the machine-zero LU noise floor — exact where theory demands it, deterministic where the mathematics fixes it.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchsci/fem-cfd@sha256:492db4d9…` (`fem-cfd` env — dolfinx 0.11.0 + PETSc + MPI, cosign-signed, `linux/arm64`) |
| input | none — mesh, forms, and manufactured solutions are all built in-code |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed (Graviton4) | x86_64 (`c8i`) |
|---|---|---|---|
| mpi_ranks | exactly 2, read from `MPI.COMM_WORLD` | 2 | 2 |
| **patch test, P2 / P3** | **L2 < 1e-12 (the space contains the quadratic)** | **1.21e-14** / 1.83e-14 | **9.46e-15** / 7.12e-15 |
| **patch test, P1 — the control** | **must be WORSE than 1e-4** | **8.235098e-03** (6.8e11×) | identical |
| **convergence rate, P2** | **finest ratio within 0.05 of 3** | **2.995, 2.999, 3.000** | identical, errors to 5 digits |
| **convergence rate, P1** | **finest ratio within 0.05 of 2** | **1.974, 1.993, 1.998** | identical, errors to 5 digits |
| error falls monotonically | every rung, both degrees | yes | yes |
| **serial vs 2 ranks** | **< 1e-6 relative on every error** | **2.434e-09** (411× inside) | **1.858e-09** (538× inside) |

**x86_64** runs the same spec on the env's linux-64 twin (`envs-x86_64/fem-cfd-49dfa59a2aff`,
installed offline, all 131 versions identical to arm64, openmpi 5.0.11). The rank count, the
discretisation errors, the rates and the P1 control agree across chips; only the machine-zero
patch-test residuals and the serial-vs-parallel difference move, both LU round-off.

### Run + verify

```sh
make run RECIPE=fenicsx
make ls  RECIPE=fenicsx
```

The checks run inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `score.tsv` and `identities.log`, each carrying both passes — the serial run and the 2-rank run, the latter cross-checking against the former. Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

**Re-verified on a new image.** The `fem-cfd` env was republished, so the pin above moved from `492db4d9…` to `941bdafc…`. A pin change is a change to the recipe's input, so every assertion was re-run rather than carried over ([why](../../practices/reference-from-tests.md)): the P2 patch test moved from 1.07e-13 to 1.21e-14 and the convergence rates are unchanged at 3.000, both at the LU noise floor where the mathematics does not care. The three checks above are new on this run.

</details>
