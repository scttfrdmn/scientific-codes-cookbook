# PETSc on six SuiteSparse SPD matrices — Graviton4, Intel and AMD

> **Four solver stacks recover the same planted solution on all six matrices, on all three chips.**
> Graviton4 is the cheapest per result billed, by 3% over AMD and 31% under Intel. AMD runs the
> whole set 1.19× faster than Graviton4, and factors the largest matrix 2.5× faster. Graviton4 is
> the cheapest box for AMG-preconditioned CG. Every iteration count is the same on all three chips.

Workload: six symmetric positive-definite matrices from the
[SuiteSparse Matrix Collection](https://sparse.tamu.edu), the standard test set for sparse solvers.
They cover heat transfer, CFD, landscape ecology, structural finite differences, a circuit, and a
3-D car-body elasticity model. They have 0.53–1.59 M rows and 3.7–77.7 M nonzeros:

| matrix | id | rows | nonzeros | kind |
|---|---|---|---|---|
| `Wissgott/parabolic_fem` | 1853 | 525,825 | 3,674,625 | CFD |
| `GHS_psdef/apache2` | 1423 | 715,176 | 4,817,870 | structural |
| `McRae/ecology2` | 1883 | 999,999 | 4,995,991 | 2D/3D |
| `Schmid/thermal2` | 1403 | 1,228,045 | 8,580,313 | thermal |
| `AMD/G3_circuit` | 1421 | 1,585,478 | 7,660,826 | circuit |
| `GHS_psdef/audikw_1` | 1252 | 943,695 | 77,651,847 | 3-D elasticity |

Each matrix is solved four ways under 16 MPI ranks. Two are direct: MUMPS symmetric-indefinite
LDLᵀ and SuperLU_DIST LU. Two are CG with algebraic multigrid: PETSc's GAMG and hypre BoomerAMG.
A million unknowns per node, solved with a direct factorization and with AMG-CG, is how PETSc is
used on one box. `audikw_1` needs 100 GiB to factor, so the set reaches the top of what one node
can do.

**Data tier: a durable public id.** Each matrix has a permanent collection id
([Davis & Hu, ACM TOMS 38(1), 2011](https://doi.org/10.1145/2049662.2049663)), and its archive is
pinned by sha256 in [`pins.sha256`](pins.sha256). The collection's Texas A&M file host has refused
every download since 2026-10-14. Its notice says this is because of excessive downloads from jobs on
AWS. [`00-fetch.task.json`](00-fetch.task.json) therefore fetches each Matrix Market archive **once**,
from the collection's original University of Florida host (archives dated 2007–2008). It keeps an
archive only if its size and entry count match the collection's published values, and every run
reads the bucket copy.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/petsc-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if not yet staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py petsc $a "$B" "$(date +%s)" \
    measurements/petsc-real/real.task.json)" --wait
done
bash measurements/petsc-real/collect.sh "$B"           # results/<chip>/, results/compare.txt
```

[`run.sh`](run.sh) converts each archive to PETSc binary ([`convert.py`](convert.py), serial), then
runs [`solve.py`](solve.py) under `mpiexec -n 16`. The four solver stacks, as `solve.py` sets them
(MUMPS also gets ICNTL(13)=1 and ICNTL(24)=1, which PETSc needs for a parallel inertia; each CG gets
`setNormType(UNPRECONDITIONED)` and `setTolerances(rtol=1e-8, atol=0.0, max_it=20000)`):

```python
SOLVERS = [
    ("mumps_ldlt", "preonly", "cholesky", "mumps"),
    ("superlu_lu", "preonly", "lu", "superlu_dist"),
    ("cg_gamg", "cg", "gamg", None),
    ("cg_hypre", "cg", "hypre", None),
]
```

## Results

`r8g.4xlarge`, `r8i.4xlarge` and `r8a.4xlarge` (16 vCPU, 128 GiB), 16 MPI ranks × 1 thread, same
staged bytes. The recipe's aarch.science `fem-cfd` image runs on Graviton4, and the env's linux-64
twin (petsc 3.25.5, MUMPS 5.8.2, SuperLU_DIST 9.2.1, hypre 3.2.0) runs on both x86 chips:

| chip | instance | tool wall | billed | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.4xlarge` | 585 s | 768 s | 99.7 GiB | 0.9426 | **0.153** | **0.201** |
| Xeon 6975P-C | `r8i.4xlarge` | 797 s | 948 s | 104.1 GiB | 1.1114 | 0.246 | 0.293 |
| EPYC 9R45 | `r8a.4xlarge` | **490 s** | **582 s** | 103.5 GiB | 1.2781 | 0.174 | 0.207 |

A result is all six matrices, each solved four ways and checked. Tool wall is the six `mpiexec`
solves, including the matrix load. Conversion takes another 19–28 s and is not counted. Billed is
EC2 launch to termination. Peak memory is the cgroup's `memory.peak`, which includes page cache and,
on x86, the env install. The largest single rank peaked at 18.6–19.0 GiB on every chip (`ru_maxrss`,
`audikw_1`).

Seconds per solver stack, setup plus solve, for the five scalar matrices and for `audikw_1`:

| solver | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| MUMPS LDLᵀ | 32.1 / 45.9 | 36.2 / 36.3 | **24.6 / 17.9** |
| SuperLU_DIST LU | 42.2 / 76.6 | 50.3 / 55.3 | **32.1 / 30.2** |
| CG + GAMG | 4.9 / 92.3 | 6.0 / 141.5 | **3.8 / 70.2** |
| CG + BoomerAMG | 12.6 / **266.0** | 19.5 / 441.8 | 12.3 / 288.2 |

- **The factorizations favour AMD.** Both direct codes run 1.3× faster on EPYC than on Graviton4 on
  the five smaller matrices, and 2.5× faster on `audikw_1`. MUMPS on `audikw_1` is 1.9× cheaper there. The dense frontal kernels run in OpenBLAS: `neoversev2` on Graviton4
  and the Intel-named `Cooperlake` on both x86 chips. Intel, with 8 cores × 2 threads, also beats
  Graviton4 on the `audikw_1` factorizations.
- **AMG-preconditioned CG favours Graviton4 per dollar.** On BoomerAMG's 1,401 iterations on
  `audikw_1`, Graviton4 is 8% faster than AMD and 1.66× faster than Intel. It costs $0.070, against
  $0.102 on AMD and $0.136 on Intel. Sparse matrix-vector work is bandwidth-bound, and
  Intel's 16 ranks share 8 cores.
- **One solve sets the bill.** BoomerAMG on `audikw_1` is 45–59% of the tool wall on every chip.
  Without it, AMD would lead on both time and cost.
- **Timings vary between runs.** An earlier Graviton4 run gave GAMG's `audikw_1` solve in 69.7 s
  against 92.3 s here, with the same 830 iterations. n = 1 per chip.
- **Overhead is 92–183 s** of each bill. Graviton4 pulls the image. Each x86 run installs the env
  twin's 131 packages offline.

## What held at scale, on all three chips

[`solve.py`](solve.py) asserts each row inside the task, so a failure fails the run. The right-hand
side is `b = A x*` for a planted `x*`, a splitmix64 hash of the global row index. It is the same
vector at any rank count, so every solve's true error is known.

| claim | why it must hold | worst over 6 matrices × 3 chips |
|---|---|---|
| rows and nonzeros equal the collection's published values | the release | 6 of 6 exact |
| PETSc reports 16 ranks | rank-count guard, [practices/mpi-rank-count.md](../../practices/mpi-rank-count.md) | 16 |
| **MUMPS LDLᵀ inertia is (0, 0, n)** | **the collection says positive definite. Sylvester's law of inertia then allows no negative or zero pivot.** | **(0, 0, n) on all 18** |
| both factorizations backward stable: `‖b−Ax‖₁ / (‖A‖₁‖x‖₁ε) < 30` | LAPACK's `dpot02`/`dget02` test ratio and its threshold | 0.614 |
| CG's converged reason > 0 **and** the recomputed `‖b−Ax‖/‖b‖ ≤ 1e-8` | CG tests the unpreconditioned norm, so its convergence claim is about this quantity | 9.67e-9 |

Reported, not asserted. The two direct codes land within **9.0e-12** of `x*` and within **1.7e-11**
of each other, worst on `ecology2` and `audikw_1`. The CG solutions sit 3e-7 to 2.3e-3 from `x*`.
That is the rtol 1e-8 residual times each matrix's conditioning. `audikw_1` under GAMG is the
worst; the matrix ships without node coordinates, so GAMG is given no rigid-body modes.

**BoomerAMG's PETSc defaults break CG on `audikw_1`.** The first Graviton4 run stopped with
`KSP_DIVERGED_INDEFINITE_PC` at iteration 2, and the run failed as designed. A probe of seven
settings ([`hypre_probe.py`](hypre_probe.py), [`results/hypre-probe.txt`](results/hypre-probe.txt))
found one that converged: l1-scaled hybrid SOR smoothing **together with** HMIS coarsening, ext+i
interpolation and strong threshold 0.7. Neither change alone was enough. So `audikw_1` runs under this
`elasticity` profile, and the five scalar matrices run under the defaults. GAMG converged on
`audikw_1` with its defaults, in 830 iterations.

## Across chips: what is a fixed point and what is a path

[`compare.py`](compare.py) asserts two claims for every pair of chips, and both held
([`results/compare.txt`](results/compare.txt)):

- **The integers are identical:** ranks, rows, nonzeros and all six inertias.
- **Every solve is the same fixed point.** `x_A − x_B = (x_A − x*) − (x_B − x*)`. So for a probe
  vector `w`, `|x_A·w − x_B·w| ≤ (‖x_A−x*‖ + ‖x_B−x*‖)‖w‖`, using forward errors measured in each
  run. The worst ratio to that bound is 1.3e-3 for the direct solves (`thermal2`, MUMPS) and 2.4e-7
  for CG (`ecology2`, BoomerAMG): the chips agree far more closely than either agrees with `x*`.

**Every Krylov iteration count is identical on all three chips**, `audikw_1`'s 830 and 1,401
included. The bits are another matter, and two Graviton4 runs show where the differences come from.
MUMPS, SuperLU_DIST and GAMG give **different bits from run to run on the same chip**, so their
cross-chip differences cannot be put down to the chip. BoomerAMG is bit-reproducible run to run (on
the five matrices both runs solved with the same settings), and **byte-identical between Intel and
AMD** on all six. They run the same linux-64 build.
BoomerAMG differs only between the arm64 and x86 builds, and those builds solve the same fixed point.

## Caveats

- One rank count (16), one rtol (1e-8), one planted solution. No knee sweep. Rank and thread scaling
  for a sparse solve is a separate measurement from this one.
- `r8` was chosen because the cgroup peaked near 100 GiB, mostly from `audikw_1`'s factorizations.
  An `m8g.4xlarge` (64 GiB) was not tried. Without `audikw_1` the per-rank peak is 1.6 GiB.
- The specs ran with `cost_limit` 0.6 on Graviton4, scaled by `spec_arch.py` for x86, and a 1 h TTL
  as the backstop. Prices are spawn's on-demand us-west-2 rates.

## Spend

Nine launches, about **$1.10**. The three verifying runs cost $0.70. The rest was two fetches (the
first hit the 403), three Graviton4 sizing runs and the BoomerAMG probe. The sizing runs found that
`mpiexec` consumed the matrix list on stdin, that MUMPS needs ICNTL(24) for an inertia, and the
BoomerAMG failure above.
