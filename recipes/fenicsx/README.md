---
tool: fenicsx
tool_version: 0.11.0
env: fem-cfd
image: quay.io/aarchsci/fem-cfd@sha256:492db4d9c166467715d87012a15d3a41ce4ec2f29fa8d5fc8bbd562f60a500be
spawn_version: 0.104.0
---
# FEniCSx — a Poisson solve verified by patch test and convergence order

FEniCSx (`dolfinx`) solves the Poisson equation by finite elements and checks itself two ways a bug can't fake — a machine-zero patch test and the theoretical convergence rate. The catalog's first finite-element recipe, for anyone doing FEM who wants it on Graviton.

> **What this covers.** dolfinx 0.11 solving Poisson on the unit square (P2 Lagrange, method of manufactured solutions), over 2 MPI ranks — the open, runnable answer where commercial FEA/CFD (ANSYS, Abaqus, COMSOL) is license-gated and can't be. Not a benchmark; a small mesh, and the mathematics does the verifying.

## Run it

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
| P2 Lagrange elements | P1 / P3 / … | the convergence check asserts rate **p+1**; change the degree and the expected rate changes with it. |
| direct LU solve | GMRES + AMG for large problems | LU is exact and right for this size; real problems scale with a Krylov solver + preconditioner — the [scaling knee](../../patterns/sizing.md). |

**Leave the fixture:** the manufactured solution is the point — it gives an exact answer to check against, which a real problem can't. **Scale it** to your physics; the two checks (exact reproduction, correct order) travel to any PDE.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the two vCPUs are the two MPI ranks), TTL 8m, cap $0.04. The solves are sub-second; dolfinx **JIT-compiles the variational forms at runtime** (the env's gcc), so the first solve includes compilation. Boot and the 0.67 GB pull are the rest. **These timings are not compute cost.**

**Sizing:** the unit-square mesh is tiny; a real problem scales with degrees of freedom (mesh × element order) and moves to a Krylov solver across ranks, where the [scaling knee](../../patterns/sizing.md) lives. Size a real model on its DOF count.

<details>
<summary>As shipped: the two checks, why the rate not the error, pins, smoke check, run + verify</summary>

### Two checks a bug can't fake

- **Patch test — exact** (the FEM analogue of BLAST's self-hit): a degree-*p* element space represents any polynomial of degree ≤ *p* exactly, so with a manufactured **quadratic** solution the P2 L2 error is machine zero — **1.07e-13**. No band, no tolerance; a wrong assembly, quadrature, or solve breaks it at once.
- **Convergence order — fixed by theory** (conservation-class): with a manufactured `sin(πx)sin(πy)` — *not* representable in the element space — the L2 error falls as h^(p+1). For P2 the rate is **3**, observed **3.00** at every step of an N = 8→16→32→64 refinement. The rate is set by the mathematics, not the fixture, so it is exact-or-wrong: a bug converges at the wrong order, or not at all. **Asserting the rate across a refinement sequence beats asserting the error at one resolution** — the latter needs a justified band, the former is theory-fixed. Same reason the lattice constant beat total energy for [Quantum ESPRESSO](../quantum-espresso/README.md).
- **MPI**: dolfinx partitions the mesh across ranks; the recipe runs on 2 and asserts `MPI.COMM_WORLD.size == 2`, so a serial fallback can't masquerade as parallel ([rank-count guard](../../practices/mpi-rank-count.md)).

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchsci/fem-cfd@sha256:492db4d9…` (`fem-cfd` env — dolfinx 0.11.0 + PETSc + MPI, cosign-signed, `linux/arm64`) |
| input | none — mesh, forms, and manufactured solutions are all built in-code |

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed |
|---|---|---|
| mpi_ranks | exactly 2 | 2 |
| patch_test_exact | L2 < 1e-10 (P2 reproduces a quadratic) | 1.07e-13 |
| convergence_rate | each ≈ 3.0 (L2 ∝ h^(p+1), p=2) | 3.00, 3.00, 3.00 |
| error_decreases | 5.5e-4 → 1.1e-6 over N 8→64 | ok |

### Run + verify

```sh
make run RECIPE=fenicsx
make ls  RECIPE=fenicsx
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with the patch-test error and the refinement table. Re-run: `make run` launches a fresh task and overwrites this prefix — no spec edit needed.

</details>
