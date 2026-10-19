# SU2 on a real workload — NASA's NACA 0012 SA validation case, Graviton4, Intel and AMD

> **Graviton4 converges the case 1.98× faster than same-generation Intel and 1.51× faster than AMD,
> at 43% and 49% of their compute cost per result.** All three land on the same lift and drag to
> every one of the 10 digits SU2 writes, inside NASA TMR's seven-code band.

Workload: the [NASA Turbulence Modeling Resource](https://tmbwg.github.io/turbmodels/naca0012_val_sa.html)
NACA 0012 validation case — M 0.15, Re 6 million, Spalart-Allmaras — at α = 10° and 15°, on the
**897×257 C-grid (230,336 points)** that TMR's comparison table was computed on. SU2's own config for
the case (`TestCases/rans/naca0012/turb_NACA0012_sa.cfg` at v8.5.0) is used unchanged except: start
from free stream rather than its committed restart, the 897×257 grid, and the angle. Each solve runs
to upstream's own criterion, RMS density < 1e-12.

## Run it

```sh
B=$(make -s print-bucket)
bash measurements/su2-real/stage-inputs.sh "$B"     # fetches the v8.5.0 files on AWS, checks pins
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py su2 $a $B r2 measurements/su2-real/real.task.json)" --wait
done
python3 measurements/su2-real/compare.py           # cross-chip, after copying runs to results/
```

Inside the task, per angle, this is the solve:

```sh
mpirun -n 16 SU2_CFD case.cfg      # turb_NACA0012_sa.cfg, MESH_FILENAME= n0012_897-257.su2, AOA= 10
```

## Results

16 MPI ranks on a `4xlarge` (16 vCPU) of each chip, same SU2 8.5.0 (aarch.science `cfd-fv` arm64; its
linux-64 twin on both x86 chips), same staged bytes. A *result* is the validation pair, α = 10° and
15°, each converged from free stream:

| chip | instance | tool wall | billed | s/iter | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.4xlarge` | **536 s** | 647 s | **0.115** | 1.31 GiB | 0.6381 | **0.095** | **0.115** |
| Intel (Xeon 6975P-C) | `c8i.4xlarge` | 1,060 s | 1,186 s | 0.229 | 2.35 GiB | 0.7497 | 0.221 | 0.247 |
| AMD (EPYC 9R45) | `c8a.4xlarge` | 810 s | 881 s | 0.174 | 2.35 GiB | 0.8622 | 0.194 | 0.211 |

*Tool wall* is every SU2 solve in the task (the two angles, plus the 2 s regression case and 20–39 s
free-stream check); *billed* is instance launch to terminate; *s/iter* is SU2's own Performance
Summary for the α = 10° solve.

- **Graviton4 vs Intel: 1.98× faster, 57% cheaper per result.** `c8i`'s 16 vCPUs are 8 cores × 2
  hyperthreads (`threads_per_core 2`), so 16 ranks share cores pairwise; `c8g` and `c8a` vCPUs are
  full cores. That is the like-for-like rental, not a per-core comparison.
- **Graviton4 vs AMD: 1.51× faster, 51% cheaper per result**, though both rent 16 full cores. The
  c8a hour also costs 35% more.
- **The iteration count is the same on all three chips** (2,393 and 2,036), so the speed ratio is
  purely seconds per iteration — the same work, done faster.
- **Peak memory is small**: 1.31 GiB on Graviton4. The x86 figure is higher because that route
  installs the env into the container at start, and the cgroup counts its page cache.
- **Compute-dominated**: 71–126 s of boot, Docker, image pull (or env install on x86) and staging,
  around 9–18 minutes of SU2. Prices are spawn's printed on-demand us-west-2 rates. n = 1 per chip;
  the solve is deterministic (two Graviton4 runs on two instances gave identical iteration counts
  and coefficients), so the timings are of one computation per chip.

## Why this size

897×257 is the grid TMR's seven-code SA table was computed on, and the one SU2's own V&V uses, so it
is the size at which a published comparison exists. A steady 2D RANS airfoil at ~230k points is an
ordinary research run; the finer 1793×513 grid is for a grid-convergence study, which is a
different question. A polar of N angles costs N × the per-angle numbers above.

## What held at scale, on all three chips

| assertion | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| `SU2_CFD` native: ELF `e_machine` == host's | 183 / 62 | 183 | 62 | 62 |
| MPI ranks, SU2's own `Cores:`; ParMETIS partitioned | == 16 launched | 16, yes | 16, yes | 16, yes |
| converged: `All convergence criteria satisfied`, RMS ρ ≤ 1e-12 | < 8,000 iterations | 2,393 / 2,036 | same | same |
| **TMR**: CL at 10°, 15° vs the seven-code mean | ±1% | +0.29%, +0.26% | same | same |
| **TMR**: CD at 10°, 15° vs the seven-code mean | ±4% | +1.95%, +2.81% | same | same |
| **upstream regression** `turb_naca0012_sa`: CL, CD, both linear-iteration counts, heat flux at iteration 5 | == upstream's printed digits | 1.080346, 0.018385, 20, 20, 0 | same | same |
| free-stream preservation on this grid: Euler, every boundary far-field, 200 iterations | max log10(RMS ρ) ≤ −12 | −15.07 | −15.07 | −15.07 |
| **cross-chip** (`compare.py`): CL, CD vs Graviton4 | ≤ 10× last-100-iteration change | 0 | 0 | 0 |

**Why those bounds.** TMR says the seven codes "gave very close results (max 1% difference in lift,
4% difference in drag)" on this case, so that spread is the precision of the shared problem, and
SU2 is asserted to sit inside it about the seven-code mean. SU2's CD lands above every code in the
table at both angles (0.012597 vs 0.01225–0.01245; 0.021870 vs 0.02073–0.02159) while inside TMR's
stated spread — reported, because the table's own codes differ by that much. The cross-chip bound is
the iterative error: near convergence each 100 iterations shrink the CL change by a factor of 0.80
(α = 10°) or 0.57 (α = 15°), so the distance to the fixed point is under 4× the last 100-iteration
change; 10× covers a ratio up to 0.91.

**The regression case at upstream's own tolerance is a concession, stated.** SU2's harness asserts
every printed value at 1e-5. The coefficients and iteration counts match exactly on all three
chips; the four residual columns miss their own architecture's list by up to 1.1e-4 (Graviton4) and
3.1e-5 (x86). Upstream's own
`test_vals` and `test_vals_aarch64` for this case differ from each other by up to 3.5e-5 on those
columns — already beyond its 1e-5 — because they are the noise floor of a restart from a converged
solution (RMS ρ ≈ 1e-12), i.e. the bits of the build that wrote them. So residuals are reported
(`check.txt`), coefficients asserted.

## Fixed point or path

| | Graviton4 | Intel | AMD | kind |
|---|---|---|---|---|
| final CL, CD, CMz at 10° and 15° (10 significant digits) | 1.096666214, 0.01259695005, −0.005266543131 | identical | identical | fixed point: identical |
| CL along the whole α = 10° history | — | max \|Δ\| 0 | max \|Δ\| 0 | fixed point throughout |
| CL along the α = 15° history | — | max \|Δ\| 1e-9 | same as Intel | |
| RMS ρ history | — | differs from iteration 35 (10°) / 79 (15°), max \|Δ\| 2.7e-6 / 1.4e-5 | byte-identical to Intel | path |
| linear-solver residual | — | differs from iteration 69–71 | byte-identical to Intel | path |
| iterations to converge | 2,393 / 2,036 | same | same | |

- **The residual histories diverge in the last bits and the coefficients do not**, because a
  converging implicit solve is a contraction: a perturbation in the iterate is damped toward the
  same fixed point rather than amplified, the opposite of an MD trajectory. That is why every
  fixed-point claim agrees to every printed digit while the residual columns differ within the
  first 80 iterations.
- **Intel and AMD are byte-identical to each other** on every history file — the same binary, and
  both chips run OpenBLAS's `Cooperlake` kernel (`openblas_get_corename()`; Graviton4 reports
  `neoversev2`).

## Caveats

- **α = 0° is not in the converged set.** With upstream's config (CFL 1000, 20 FGMRES iterations),
  α = 0° on Graviton4 reached only RMS ρ = 10^−9.86 in 8,000 iterations, with CD still moving
  5e-7 per 100 iterations (0.0081785, against TMR's 0.00812–0.00830), so it is neither a
  converged fixed point nor a fair timing.
- **This grid is not exactly mirror-symmetric**, so α → −α is not an identity here: its wake line
  sits at y = 5.35e-8, and mirrored points miss their partners by up to 3.7e-5 (13% of the local
  spacing in the wake). In the same Graviton4 run, CL(0°) was −3.5e-6 and CL(10°) + CL(−10°) was
  −8.4e-6, about twice CL(0°) — the asymmetry showing in both — so the pair is reported, not asserted.

## Pins

| | |
|---|---|
| grid | `TestCases/rans/naca0012/n0012_897-257.su2` @ su2code/TestCases v8.5.0 (`790c80e`), sha256 `2588fe11…d68d15` |
| regression | `n0012_225-65.su2` (`20dfc968…`) and `solution_flow_sa.dat` (`3e665e2d…`) @ the same commit |
| config | `TestCases/rans/naca0012/turb_NACA0012_sa.cfg` @ su2code/SU2 v8.5.0 (`12eb826`), sha256 `fc88ad7b…` |
| reference | TMR's "Summary of CFD SA Results" table, embedded in `real.py` with its URL; regression `test_vals` from `TestCases/parallel_regression.py` @ v8.5.0 |
| image | arm64 `quay.io/aarchsci/cfd-fv@sha256:830eaf94…`; x86 the env's linux-64 twin `envs-x86_64/cfd-fv-4ac4ff422132` — the recipe's pins |
| scripts | `real.py`, `run.sh`, checked against `pins.sha256` at stage-in with the data |

**Data tier: a versioned release.** No RODA copy exists; the files are fetched on AWS by
`00-fetch.task.json` at the release commits and kept only if each equals its git blob in that tree
(`manifest.tsv`), then pinned by sha256. Raw per-chip output is in `results/`; the two large
α-solve `history.csv` files per chip are in the bucket under `runs*/su2-real/r2/`.
