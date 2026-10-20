# OpenFOAM on a real workload — NASA's NACA 0012 SA validation case, Graviton4, Intel and AMD

> **AMD finishes the case 1.23× faster than Graviton4 and Intel 1.21× slower, but Graviton4 is the
> cheapest per result: $0.72 against AMD's $0.79 and Intel's $1.02.** All three land on the same
> lift and drag to 9 significant digits, inside NASA TMR's seven-code range, on the very vertices
> [SU2](../su2-real/README.md) solved. **One assertion fails, the same way on every chip, and is reported,
> not loosened:** OpenFOAM's drag at 15° (0.020962, inside TMR's range) and SU2's (0.021870, above every
> TMR code) differ by 4.15% against a ±4% cross-code bound. That is exactly the spread between TMR's own
> two widest codes in that column, so each task exits 1 by design.

Workload: the [NASA Turbulence Modeling Resource](https://tmbwg.github.io/turbmodels/naca0012_val_sa.html)
NACA 0012 validation case — M 0.15, Re 6 million, Spalart–Allmaras, adiabatic wall — at α = 10° and
15°, on the **897×257 C-grid (229,376 cells)** TMR's comparison table was computed on. The grid is
su2-real's staged `n0012_897-257.su2`, converted to an OpenFOAM `polyMesh` one cell thick with every
vertex copied as SU2's own coordinate text, so the two codes solve on identical geometry.
`rhoSimpleFoam` (compressible, steady), OpenFOAM's `SpalartAllmaras`, 20,000 SIMPLE iterations per
angle from free stream.

## Run it

```sh
B=$(make -s print-bucket)
bash measurements/su2-real/stage-inputs.sh "$B"        # the grid, pinned there
bash measurements/openfoam-real/stage-inputs.sh "$B"   # these scripts; checks the grid's pin
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py openfoam $a $B r3 measurements/openfoam-real/real.task.json)" --wait
done
python3 measurements/openfoam-real/compare.py <dir>    # <dir>/<chip>/ = the three runs*/openfoam-real/r1/ prefixes
```

Inside the task, per angle (`case.sh` writes the dictionaries from TMR's M, Re and T):

```sh
decomposePar                                            # hierarchical (4 4 1)
mpirun --oversubscribe -np 16 rhoSimpleFoam -parallel   # 500 iterations SIMPLE, then SIMPLEC to 20000
```

## Results

16 MPI ranks on a `4xlarge` (16 vCPU) of each chip, the recipe's pinned `opencfd/openfoam-default`
v2412 (one manifest list: its arm64 and amd64 entries), same staged bytes, same partition. A *result*
is the validation pair, α = 10° and 15°:

| chip | instance | tool wall | billed | ms / GAMG iteration | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | 3,990 s | 4,066 s | 1.56–1.59 | 1.08 GiB | 0.6381 | **0.707** | **0.721** |
| Intel (Xeon 6975P-C) | `c8i.4xlarge` | 4,834 s | 4,911 s | 1.86–1.93 | 1.07 GiB | 0.7497 | 1.007 | 1.023 |
| AMD (EPYC 9R45) | `c8a.4xlarge` | **3,247 s** | 3,312 s | **1.24–1.29** | 1.07 GiB | 0.8622 | 0.778 | 0.793 |

*Tool wall* is both angles' `rhoSimpleFoam` (both stages); *billed* is spawn's task Started → Ended;
*ms / GAMG iteration* is solver time over the pressure solver's total iterations, the unit of work
(`compare.txt`); peak memory is cgroup `memory.peak`.

- **AMD vs Graviton4: 1.23× faster, 10% dearer per result.** Both rent 16 full cores
  (`threads_per_core 1`); the c8a hour costs 35% more, which outruns its speed.
- **Intel vs Graviton4: 1.21× slower, 42% dearer per result.** `c8i`'s 16 vCPUs are 8 cores × 2
  hyperthreads, so the 16 ranks share cores pairwise — the like-for-like rental, not a per-core race.
- **The work is nearly the same on every chip**: 20,000 SIMPLE iterations each, and 1.338M (Graviton4)
  vs 1.363M (x86) pressure-solver iterations at α = 10°, so the per-iteration rate is the comparison.
  The ranking is not SU2's (there Graviton4 was fastest outright): a GAMG-dominated solve favours AMD.
- **The partition is a bigger dial than the chip.** An earlier run of the same case with `scotch` took
  3,022 / 4,164 / 2,901 s — 1.12–1.32× faster than `hierarchical` on every chip — but scotch cut a
  different partition on each box (even Intel and AMD, running one binary), which makes every
  cross-chip difference unattributable. This run trades that speed for attribution; for your own
  runs, use scotch.
- **Compute-dominated**: 65–77 s of boot, image pull and staging against 54–81 minutes of solve.
  Prices are spawn's printed on-demand us-west-2 rates (as in su2-real). n = 1 per chip; the solve is
  deterministic for a given partition (Intel and AMD produced byte-identical coefficient histories),
  so each timing is of one computation.

## Why this size

897×257 is the grid TMR's seven-code SA table was computed on, so it is the size at which a published
comparison exists, and the one SU2's V&V uses. A steady 2D RANS airfoil at ~230k cells is an ordinary
research run; the 1793×513 grid is for a grid-convergence study, a different question. A polar of N
angles costs N × half of the per-result numbers above.

## What held at scale, on all three chips

| assertion | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| `rhoSimpleFoam` native: ELF `e_machine` == host's | 183 / 62 | 183 | 62 | 62 |
| MPI ranks, OpenFOAM's own `nProcs` | == 16 launched | 16 | 16 | 16 |
| mesh: `checkMesh` cells, points; topology, openness, pyramids, volumes | 229,376 / 460,672; 9 of 9 OK | yes | yes | yes |
| converged: last two 1000-iteration changes shrinking; estimated remaining iterative error | < 1/10 of TMR's band (1e-3 CL, 4e-3 CD) | ≤ 1.1e-5 CL, ≤ 2.2e-4 CD | ≤ 7.7e-6, ≤ 2.9e-5 | ≤ 7.7e-6, ≤ 2.9e-5 |
| mass: net farfield flux / Σ\|flux\| (the wall carries none) | < 1e-9 | ≤ 2.7e-12 | ≤ 2.9e-12 | ≤ 2.9e-12 |
| **TMR**: CL at 10°, 15° vs the seven-code mean | ±1% | −0.24%, −0.05% | same | same |
| **TMR**: CD at 10°, 15° vs the seven-code mean | ±4% | −0.98%, −1.46% | same | same |
| **SU2, same vertices**: CL at 10°, 15° | ±1% | −0.52%, −0.31% | same | same |
| **SU2, same vertices**: CD at 10° | ±4% | −2.88% | same | same |
| **SU2, same vertices**: CD at 15° | ±4% | **−4.15% — FAIL** | same | same |
| **cross-chip** (`compare.py`): CL, CD vs Graviton4 | ≤ 10× last-1000-iteration change | ≤ 7e-9 | ≤ 7e-9 | ≤ 7e-9 |
| partition: cells per rank identical on all chips | == | yes | yes | yes |

**Why those bounds.** TMR says its seven codes "gave very close results (max 1% difference in lift,
4% difference in drag)", so that spread is the precision of the shared problem: OpenFOAM is asserted
to sit inside it about the seven-code mean, and OpenFOAM and SU2 to differ by no more than two TMR
codes do. The convergence bound is iterative error a tenth of that band, estimated from the
contraction ratio of successive 1000-iteration changes; the cross-chip bound is su2-real's.

**The one failure is a cross-code finding, the same on every chip.** OpenFOAM's CD at 15°
(0.020962) is inside TMR's range (0.02073–0.02159); SU2's (0.021870) is above every TMR code, as
su2-real reports. The two bracket the table, 4.15% apart — and TMR's own widest pair in that column
(FUN3D 0.02159 vs GGNS 0.02073) is 4.15% apart too, so TMR's "4%" is a rounded summary of its range,
not a tolerance either code was built to. The assertion is left as written and the task exits 1 on it.
OpenFOAM sits 0.3–4.2% below SU2 in every coefficient on the same vertices: a cell-centred and a
vertex-centred discretisation of one grid are different discrete problems.

## Fixed point or path

| | Graviton4 | Intel | AMD | kind |
|---|---|---|---|---|
| final CL, CD at 10° | 1.0909207487, 0.0122344482 | 1.0909207494, 0.0122344483 | identical to Intel | fixed point: 7e-10 apart |
| final CL, CD at 15° | 1.5509941041, 0.0209618702 | 1.5509941111, 0.0209618664 | identical to Intel | fixed point: 7e-9 apart |
| coefficient history | — | differs from iteration 1, max \|Δ\| 1.1e-6 (10°), 2.8e-6 (15°) | byte-identical to Intel | path |
| pressure-solver iterations, α = 10° | 1,337,977 | 1,363,253 | 1,363,253 | path |

- **The histories differ from the first iteration and converge to the same point**, because a
  converging SIMPLE solve is a contraction: last-bit differences in the iterate are damped toward the
  fixed point rather than amplified. The final coefficients agree ≥10⁴× tighter than the cross-chip
  bound.
- **Intel and AMD are byte-identical** on every coefficient — one binary, one partition. The
  arm64/x86 difference is the build's floating point, not the chip vendor. The run's linear algebra is
  OpenFOAM's own `lduMatrix` solvers (GAMG, PBiCGStab), so there is no OpenBLAS kernel to report.
- With scotch the cross-chip CL spread was 4e-6 instead of 7e-10: the partition sets the GAMG
  agglomeration, and a different one is a different (equally converged) iteration.

## Caveats

- **Not driven to residual convergence.** The pressure, momentum and energy initial residuals end at
  2–13e-9, but `nuTilda`'s stalls at 2–4e-4 from iteration ~1,000 on, so no residual criterion is met
  and every chip runs exactly 20,000 iterations; convergence is asserted on the coefficients instead.
- **The start is the tutorial's, then SIMPLEC.** An impulsive SIMPLEC start diverged in five
  iterations, so the first 500 use the relaxation of OpenFOAM's own `rhoSimpleFoam/aerofoilNACA0012`
  tutorial (p 0.7, ρ 0.01), and the rest SIMPLEC (`consistent yes`, U/e/ν̃ 0.9).
- **Model and gas follow SU2's defaults, not every TMR detail:** Sutherland viscosity with SU2's
  constants, OpenFOAM's Eucken conductivity (Pr ≈ 0.69 vs 0.72) and Prt 0.85; at M 0.15 with an
  adiabatic wall the stagnation temperature rise is 0.45%, so they barely touch density or viscosity.

## Pins

| | |
|---|---|
| grid | `n0012_897-257.su2` @ su2code/TestCases v8.5.0, sha256 `2588fe11…1337e`, read from su2-real's staged copy |
| reference | TMR's "Summary of CFD SA Results" (as embedded in su2-real); SU2 8.5.0's coefficients from `../su2-real/results/arm64/results.tsv` |
| image | the recipe's: arm64 `opencfd/openfoam-default@sha256:b8b674f7…`, amd64 `@sha256:adbf625a…` |
| scripts | `run.sh`, `case.sh`, `su2ToFoam.awk`, checked with the grid against `pins.sha256` at stage-in |

**Data tier: a versioned release**, staged by su2-real. Per-chip output is in `results/` (the check,
host facts, partition, a 1-in-100 coefficient history); the full logs and per-iteration histories
are in the bucket under `runs*/openfoam-real/r1/`.
