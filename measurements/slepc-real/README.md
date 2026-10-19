# SLEPc at 2.1 million unknowns: the lowest 102 modes against an exact spectrum, on Graviton4, Intel and AMD

> **SLEPc 3.25.2 finds all 102 of the lowest eigenvalues of a 128³ Laplacian on all three chips.**
> Each one matches its closed form to within 0.2 ε‖A‖, with every degenerate cluster at its exact
> multiplicity. An independent LDLᵀ inertia count shows nothing below the cut was missed. AMD is the
> fastest and the cheapest per result; Graviton4 is next. The eigenvalues agree across chips to
> 9e-17. The phases do not scale together: Graviton4 factorises slowest and solves fastest.

Workload: the 7-point Dirichlet Laplacian on a **128 × 128 × 128** grid, which gives **2,097,152
unknowns** and 14.6M nonzeros, distributed over **16 MPI ranks**. The job is the lowest **102**
eigenpairs. This is the standard way SLEPc is used for the low modes of a large FEM/FD operator:
Krylov-Schur with shift-and-invert about 0, on a parallel MUMPS Cholesky factorisation. A 2M-dof,
~100-mode modal problem is what researchers run on one node. It is also near the ceiling for a
direct factorisation on a 128 GiB box (61 GiB peak, below).

**Data tier: build, and the reference is mathematics.** On a cube the eigenvalues are
`λ(k₁,k₂,k₃) = Σ_d 4 sin²(k_d π / 2(N+1))` exactly, for any N. Their multiplicities (1, 3 or 6,
from permutations of the k's) are known exactly too. Missing a copy of a repeated eigenvalue is the
classic way a Krylov eigensolver fails, and this operator tests for it 27 times over. No public
matrix of this size comes with its spectrum. SuiteSparse would have given a real FEM pair but no
reference, and its file server has answered 403 to every download since 2026-10-14. Nothing is staged except the
two scripts.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/slepc-real/stage-inputs.sh "$B"     # checks the closed form against dense eigvalsh, stages the scripts
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py slepc $a "$B" "$(date +%s)" \
    measurements/slepc-real/real.task.json)" --wait
done
bash measurements/slepc-real/collect.sh "$B"           # results/<chip>/, results/compare.txt
```

[`real.py`](real.py) is the workload and its checks, and [`real.sh`](real.sh) wraps it:
`mpiexec --use-hwthread-cpus --bind-to hwthread -n 16`, with one BLAS thread per rank. The solve:

```python
eps.setProblemType(SLEPc.EPS.ProblemType.HEP); eps.setDimensions(nev=102)
eps.setTarget(0.0); eps.setWhichEigenpairs(SLEPc.EPS.Which.TARGET_MAGNITUDE)
st.setType("sinvert"); pc.setType("cholesky"); pc.setFactorSolverType("mumps")
```

## Results

`r8g.4xlarge`, `r8i.4xlarge` and `r8a.4xlarge` (16 vCPU, 128 GiB), 16 ranks on each. The recipe's
image runs on Graviton4. The `fem-cfd` env's linux-64 twin (`envs-x86_64/fem-cfd-1950e1068faa`,
installed offline) runs on both x86 chips.

| chip | instance | tool wall | billed | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 656 s | 760 s | 60.7 GiB | 0.9426 | 0.1718 | 0.1990 |
| Xeon 6975P-C | `r8i.4xlarge` | 687 s | 737 s | 63.2 GiB | 1.1114 | 0.2121 | 0.2275 |
| **EPYC 9R45** | `r8a.4xlarge` | **469 s** | **509 s** | 63.6 GiB | 1.2781 | **0.1665** | **0.1807** |

A result is the whole verified run: the 102-pair shift-and-invert solve, two inertia
factorisations, a second solver, and every check. Peak memory is the cgroup's `memory.peak`. The 16
ranks' `ru_maxrss` sum to 61.3–61.9 GiB on all three chips, so the footprint is the MUMPS factor,
not the chip.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| Krylov-Schur + shift-and-invert, 102 pairs (one factorisation + the solves) | 276.6 | 416.3 | 312.1 |
| one MUMPS LDLᵀ factorisation (each inertia count) | 167.4 | 94.9 | 45.2 |
| Generalized Davidson + hypre BoomerAMG, 20 pairs, no factorisation | 38.2 | 74.9 | 60.5 |

- **AMD wins on cost by 9% billed over Graviton4, though its hour is 36% dearer**, because it runs
  the job 1.40× faster. Intel is the slowest and the dearest, 14% above Graviton4 billed. Its 16
  vCPU are 8 cores × 2 threads, so 16 ranks share 8 cores; Graviton4 and AMD give 16 full cores.
- **The phases rank the chips differently.** One factorisation takes 167 s on Graviton4, 95 s on
  Intel and 45 s on AMD. That is dense frontal-matrix BLAS-3, and both x86 chips run OpenBLAS's
  AVX-512 `Cooperlake` kernel. On the Krylov phase the order flips: subtracting one
  factorisation leaves ~110 s of triangular solves and orthogonalisation on Graviton4, against
  ~320 s on Intel and ~270 s on AMD. *This split is an inference*: it assumes the shift-0
  factorisation inside the solve costs the same as a shifted one, which was not timed separately.
- **The right chip depends on the phase mix.** A job that factorises once and solves for many
  modes leans toward Graviton4. Spectrum slicing, with many shifts and a factorisation each, leans
  hard toward AMD. The factorisation-free solver (GD + AMG) is fastest on Graviton4.
- **Overhead is 40–104 s of each bill**: boot, Docker, and either the 42 s image pull on Graviton4
  or the offline env install on x86.
- n = 1 per chip. The solve is not bit-reproducible run to run, even on one chip: two Graviton4 runs
  gave orthonormality 1.10e-14 and 2.24e-14. The fixed point is reproducible and the path is not,
  so the timings are single samples of a fixed amount of work.

## What held at scale, on all three chips

| check | assertion | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| MPI ranks, read from inside | == 16 launched | 16 | 16 | 16 |
| rank 0 ownership | strict subset | 131,072 of 2,097,152 rows | same | same |
| converged | ≥ 102 | 138, in 4 restarts | 138, in 4 | 128, in 5 |
| **each θᵢ vs exact λᵢ** | **≤ ‖rᵢ‖²/δᵢ + 64 ε‖A‖** (Rayleigh-quotient bound) | worst 4.58e-16 | 4.68e-16 | 4.89e-16 |
| **multiplicities** | **all 27 clusters exact** | 1,3,3,3,1,6,… all match | all match | all match |
| **Sylvester inertia, LDLᵀ of A − bI** | **102 below the cut, 44 below the mid shift, 0 zero pivots** | 102 / 44 / 0 | 102 / 44 / 0 | 102 / 44 / 0 |
| **orthonormality** | **max \|XᵀX − I\| ≤ 1e-11** | 2.24e-14 | 1.35e-14 | 1.29e-14 |
| second solver vs exact | each within its own residual | 1.95e-15 | 9.83e-16 | 4.72e-16 |
| **second solver vs Krylov-Schur** | **within the sum of residuals** | 2.34e-15 | 1.39e-15 | 6.98e-16 |

- **Rounding is the binding term, not the residual.** The residuals are ≤ 1.6e-10, and the
  narrowest gap δ is 4.2e-6, so the quadratic bound ‖r‖²/δ is ~1e-14 or smaller. Every eigenvalue
  sits about **4.0e-16 below** its closed form, a near-constant offset across all 102. That is
  0.15 ε‖A‖ (ε‖A‖ = 2.7e-15): the accuracy floor of a backward-stable factorisation, and well
  inside the 64 ε‖A‖ allowance. Relative to the eigenvalue it is at most 2.3e-13, on the smallest.
- **Inertia is the "nothing missed" check, and it is independent.** It comes from a separate MUMPS
  LDLᵀ of the shifted matrix, not from the eigensolver. The cut sits midway in the gap above the
  102nd eigenvalue. A solver that skipped one copy of a 6-fold cluster would return 102 values that
  pass every residual check. It would fail this one and the multiplicity check.
- **Two algorithms, one answer.** The 20 lowest values come again from generalized Davidson with a
  hypre BoomerAMG preconditioner: no factorisation, a different method, a different library for the
  preconditioner. It agrees with Krylov-Schur to ≤ 2.3e-15 on every chip.
- [`stage-inputs.sh`](stage-inputs.sh) **checks the reference, not the solver.** It diagonalises the
  same operator densely on 9³, 12³ and 14³ cubes, and the closed form, the multiplicities and the
  inertia cut must all match. The 14³ case is why clusters are grouped by *value*, not by index
  multiset: at N = 14, `(1,1,5)` and `(1,3,4)` coincide exactly (a trig identity at π/30), giving
  one 9-fold eigenvalue. At N = 128 there is no such coincidence among the lowest 27 clusters. The
  closest pair is `(2,2,5)`/`(1,4,4)` at 2.2e-4 relative.

## What differed across chips: path, not fixed point

[`results/compare.txt`](results/compare.txt):

| | Graviton4 vs Intel | Graviton4 vs AMD | Intel vs AMD |
|---|---|---|---|
| max \|Δθ\| over 102 eigenvalues (fixed point, asserted) | 9.0e-17 | 7.3e-17 | 8.0e-17 |
| bit-identical eigenvalues | 6 of 102 | 3 of 102 | 16 of 102 |
| inertia counts (asserted) | identical | identical | identical |
| Krylov-Schur restarts / converged (path) | 4 / 138 vs 4 / 138 | 4 / 138 vs 5 / 128 | 4 / 138 vs 5 / 128 |
| GD iterations (path) | 390 vs 391 | 390 vs 387 | 391 vs 387 |
| `openblas_get_corename()` | `neoversev2` vs `Cooperlake` | `neoversev2` vs `Cooperlake` | both `Cooperlake` |

The eigenvalues are a converged fixed point. They agree across chips to under 0.04 ε‖A‖, far
inside what each chip's own residual allows. Iteration counts, the number converged past 102,
residuals and orthonormality are the path, and they move. OpenBLAS 0.3.34 has a kernel for neither
the Xeon 6975P-C (Granite Rapids) nor the EPYC 9R45 (Zen 5): both fall back to `Cooperlake`. Intel
and AMD therefore run the same BLAS kernels, and they still differ in the last bits. The solve is
not bit-reproducible even on one chip (above), so this is not evidence of a kernel difference.

## Sizing

Measured on Graviton4 `r8g.4xlarge` with 16 ranks ([`probe.task.json`](probe.task.json)), before
the real run:

| N | unknowns | Krylov-Schur phase | peak memory |
|---|---|---|---|
| 48 | 110,592 | 6 s | 3.2 GiB |
| 80 | 512,000 | 39 s | 19.3 GiB |
| 128 | 2,097,152 | 277–301 s | 60.7 GiB |

At 128³ the job needs the `r8` family. An `m8` 4xlarge has 64 GiB, below the 63.6 GiB peak once the OS is counted. TTL
was 2 h as a backstop. `cost_limit` was $1.50 at Graviton4 prices, scaled by `spec_arch.py` for x86.

## Caveats

- **The second solver is GD + hypre, and only that pairing is verified.** `real.py` also accepts
  `SLEPC_REAL_ALT=lobpcg-gamg` and `jd-hypre`, and neither runs. LOBPCG applies its preconditioner
  to a block of vectors: through `PCMatApply_HYPRE_BoomerAMG` that returns hypre error 1, and with
  GAMG it segfaults even at 48³. JD needs an iterative KSP, not `preonly`.
- **The cross-chip agreement is on one operator.** A 7-point Laplacian is well conditioned for its
  size (κ ≈ 6,700). An ill-conditioned FEM pair would widen the residuals, but not the fact that
  the fixed point holds.

## Specs and raw output

`real.task.json` (arm64; `scripts/spec_arch.py` derives the x86 and AMD specs from it), `probe.task.json`
(the sizing ladder). Per chip, in `results/<chip>/`: `host.txt` (CPU, threads per core, cgroup peak)
and `N128/` (`real.log`, `score.tsv`, `eigenvalues.tsv` with every θ, its exact value, error, residual
and cluster, and `times.txt`).
