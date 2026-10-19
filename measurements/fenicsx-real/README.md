# FEniCSx on a real workload — 17 million P2 unknowns in 3D, three chips

> **A 3D Poisson solve at 16,974,593 degrees of freedom converges at exactly the theoretical order
> (L2 3.0000, H1 1.9994) on Graviton4, Intel and AMD, and two unrelated multigrid codes reach the
> same discrete solution to 2.6e-8 of the discretisation error.** Graviton4 is the cheapest per
> result; AMD matches its speed at a dearer hour; Intel is 1.59× slower at 32 ranks, because its
> 32 vCPUs are 16 cores.

Workload: the method of manufactured solutions on the unit cube, `u = sin πx sin πy sin πz`,
solved the way a large FEM run is solved — **P2 tetrahedra, CG preconditioned by hypre BoomerAMG,
over 32 MPI ranks**, with a refinement ladder up to **N = 128: 12,582,912 tetrahedra and
16,974,593 unknowns**. The same finest system is then solved a second time with PETSc's GAMG. A P1
ladder (to 2.1 M unknowns), a direct-solve containment test, and a serial pass (to 2.1 M unknowns)
ride along. Data tier: synthetic — the reference is the exact solution, so there is nothing to
download, and none of the checks needs a band.

**Why this size.** 17 M unknowns is a production single-node FEM solve, and it is the size that
fills the box: peak memory is **108–111 GiB of the 128 GiB** on all three chips. A bigger rung
(N = 160 is 33 M unknowns) needs the next instance size up; the knee to find there is the DOF count
per GiB, not the core count.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/fenicsx-real/stage-inputs.sh "$B"      # two scripts, sha256-pinned; no data
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py fenicsx $a "$B" r1 measurements/fenicsx-real/real.task.json)" --wait
done
bash measurements/fenicsx-real/collect.sh "$B" r1        # results/<chip>/ and the cross-chip comparison
```

The task runs `real.sh`, which runs `poisson3d.py` serially and then under
`mpiexec -n 32 --oversubscribe`. The solve itself is the recipe's, in 3D, with the production
solver:

```python
msh = mesh.create_unit_cube(comm, 128, 128, 128, cell_type=mesh.CellType.tetrahedron)
V = fem.functionspace(msh, ("Lagrange", 2))
LinearProblem(a, L, bcs=[bc], petsc_options_prefix="p2_", petsc_options={
    "ksp_type": "cg", "ksp_norm_type": "unpreconditioned", "ksp_rtol": 1e-12, "ksp_atol": 0.0,
    "pc_type": "hypre", "pc_hypre_type": "boomeramg"}).solve()      # then "pc_type": "gamg"
```

## Results

32 MPI ranks × 1 thread on an `m8*.8xlarge` (32 vCPU, 128 GiB) of each chip. dolfinx 0.11.0, PETSc
3.25.6, hypre 3.2.0, Open MPI 5.0.11: aarch.science's `fem-cfd` image on Graviton4, its linux-64
lock twin on both x86 chips.

| chip | instance | tool wall (serial + 32-rank) | finest P2 solve (BoomerAMG / GAMG) | task window | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `m8g.8xlarge` | **168 s** (76 + 92) | 40.5 s / 20.5 s | 267 s | 108.2 GiB | 1.4362 | **0.067** | **0.107** |
| Xeon 6975P-C | `m8i.8xlarge` | 240 s (94 + 146) | 67.6 s / 35.4 s | 283 s | 111.1 GiB | 1.6934 | 0.113 | 0.133 |
| EPYC 9R45 | `m8a.8xlarge` | 164 s (72 + 92) | 43.1 s / 21.4 s | 209 s | 111.4 GiB | 1.9475 | 0.089 | 0.113 |

A *result* is the whole run: every rung, both preconditioners, both passes. *Compute* prices the
two python passes; *billed* is spawn's `cost_estimate` over the task window.

- **Graviton4 and AMD run the 32-rank pass in the same 92 s; Intel takes 146 s.** Intel's 32 vCPUs
  are 16 cores × 2 threads (`lscpu`, `results/*/host.txt`), so 32 ranks share 16 cores. Graviton4
  and AMD vCPUs are full cores. Per result Graviton4 is cheapest: AMD costs 32% more compute and 6%
  more billed, Intel 69% and 25% more.
- **GAMG solves the finest system twice as fast as BoomerAMG** on every chip (20.5 vs 40.5 s on
  Graviton4) despite needing 53 CG iterations to BoomerAMG's 12. PETSc defaults for both; neither
  was tuned. For a 3D P2 Poisson problem of this size, try `pc_type gamg` first.
- **Memory decides the box.** The cgroup peak is 108–111 GiB on 128 GiB, about **6.7 KiB per P2
  unknown** at 32 ranks, including the ~0.35 GiB each rank costs before any mesh exists. The
  x86 peak is ~3 GiB higher because the installed env's page cache is counted. A
  `c8g.8xlarge` (64 GiB) cannot hold this rung. The probe that sized it ran the full workload on
  this box.
- **Overhead is 43–99 s** of each window: boot, image pull or the offline env install, staging.
  These timings are not compute cost ([layout](../../patterns/layout-and-effective-cost.md)).
- n = 1 per chip, plus one Graviton4 probe with identical configuration: 79 + 91 s against 76 + 92
  s, so run-to-run noise is about 4% per pass. Every discrete count below is identical across runs.

## What held at scale, on all three chips

| claim | why it must hold | all three chips |
|---|---|---|
| L2 error falls as h³ and H1 as h² (P2) | theory for a degree-2 space and a smooth solution | finest orders **3.0000** and **1.9994** |
| L2 as h² and H1 as h (P1) | the same, degree 1 | **1.9992** and **0.9997** |
| every rung's error is below the last | refinement must help | yes, both degrees, both norms |
| P2 and P3 reproduce `1 + x² + 2y² + 3z²` | the space contains it, so Galerkin is exact (MUMPS LU) | L2 3.9e-15 to 3.4e-14, bound 1e-10 |
| P1 does not (the control) | P1 cannot represent a quadratic | 1.622126e-02, identical on all three |
| BoomerAMG and GAMG reach one discrete solution | the FE solution is the system's fixed point, whatever the preconditioner | ‖u_amg − u_gamg‖ = 4.5e-15 to 9.1e-15, **2.6e-8 to 5.3e-8 of the discretisation error** (bound 1e-3) |
| serial and 32 ranks agree on every shared rung | partitioning changes summation order, not the answer | worst 3.0e-10 to 5.6e-10 relative (bound 1e-6), 6 rungs |
| `MPI.COMM_WORLD.size == 32` | a nompi build would run 32 serial copies that agree vacuously ([rank-count guard](../../practices/mpi-rank-count.md)) | 32 |

The preconditioner check is what licenses the order check: their difference *is* the algebraic
error, and at 2.6e-8 of the discretisation error the measured 3.0000 is the method's order, not the
solver's.

## Across chips: fixed points and paths

**Identical on all three chips:** every DOF and cell count, every CG iteration count (12 BoomerAMG
and 53 GAMG at the finest rung, the same on every rung below), every convergence order to the four
printed decimals, and the P1 control to all 13 digits.

**The L2 errors move by a few ulps of the solution, and they move in the assembled system, not in
the solver.**

| rung | Graviton4 L2 | Intel L2 | AMD L2 | x86 vs Graviton4 |
|---|---|---|---|---|
| P1, N = 16 and 32, 32 ranks | 6.337552439127e-03, 1.597641078174e-03 | same | same | bit-identical |
| P2, N = 64 | 1.370897387263e-06 | 1.370897393243e-06 | 1.370897393248e-06 | 4.4e-9 relative |
| P2, N = 128 (BoomerAMG) | 1.713647784738e-07 | 1.713647905436e-07 | 1.713647905389e-07 | 7.0e-8 relative |
| P2, N = 128 (GAMG) | 1.713647784338e-07 | 1.713647904201e-07 | 1.713647904169e-07 | 7.0e-8 relative |

- On each chip the two preconditioners agree to 2.3e-10 relative, but the x86 chips sit 7e-8 away
  from Graviton4 under *both*. So the difference is in the discrete system each chip assembles,
  which both solvers then converge to.
- In absolute terms it is **1.2e-14** on a solution of norm ~0.35, about 3e-14 relative to `u`.
  A relative difference on an *error* is large only because the error is small. The H1 errors agree
  to 4.4e-12. Intel and AMD agree with each other to 3e-11.
- **It is not FMA contraction in the JIT-compiled kernels.** aarch64 contracts `a*b+c` into FMA by
  default and baseline x86-64 cannot, so that was the first suspect. `fpcontract.task.json` solves
  the serial P2 N = 64 rung with the default flags and with `-ffp-contract=off`, each in its own
  JIT cache. Both give **1.370897387680e-06** on Graviton4 and **1.370897392480e-06** on Intel, which
  are the serial-pass values exactly. The source is somewhere in the compiled libraries or libm and
  is not attributed further here.
- The machine-zero containment residuals (4e-15 vs 1.6e-14) and the algebraic error (2.6e-8 vs
  5.3e-8 of the discretisation error) differ too. Both are round-off floors and are bounded, not
  compared.
- OpenBLAS is not on this path, but `openblas_get_corename()` is recorded: `neoversev2` on
  Graviton4, `Cooperlake` on both x86 chips.

**One observation for a dolfinx user.** The H1 error of the exactly-represented quadratic in P3 is
**2.319204e-06** at quadrature degree 9 on every chip and pass, against **1–2e-13** at degree 4. Degree 4 is
exact for `|∇e|²` in P3, and the L2 error at degree 9 is 5e-15. So the degree-9 tetrahedral
scheme, not the solution, produces that number. It does not touch the ladder: the P2 rungs use
degree 7, where the same comparison gives 1.5e-13 against 1.9e-13. It is reported, not asserted.

## Pins (data tier: synthetic)

| | |
|---|---|
| data | none: the mesh, the forms and the exact solutions are built in-code |
| image | arm64 `quay.io/aarchsci/fem-cfd@sha256:941bdafc…`; x86 the `fem-cfd-49dfa59a2aff` lock twin (the recipe's frontmatter) |
| task | `real.sh` and `poisson3d.py`, pinned in `pins.sha256` and re-checked inside the task before anything runs; `fpcontract.py` in `fpcontract.sha256` |

## Caveats

- Prices are on-demand us-west-2: spawn's `cost_estimate` over the task window. `$/hr` is that
  estimate over the window.
- Peak memory is the container's cgroup `memory.peak`. The sum of per-rank `ru_maxrss`
  (110.9–116.8 GiB) is not a concurrent peak, because ranks peak at different moments.
- Both preconditioners use PETSc defaults. A tuned BoomerAMG (strong threshold, aggressive
  coarsening) may close the gap to GAMG. The comparison here is of what a reader gets
  out of the box.
