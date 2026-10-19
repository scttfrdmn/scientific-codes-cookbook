# SIESTA on its own test suite and a 1,359-atom quantum dot — Graviton4, Intel and AMD

> **SIESTA 5.4.2 runs its whole regression suite and converges a 1,359-atom silicon quantum dot at
> the production DZP basis, on all three chips.** Every test whose SCF converged matches SIESTA's
> committed reference (112 of 112 on each chip), and the converged dot's total energy agrees across
> chips to 1.3e-5 eV. AMD EPYC runs the job 1.9× faster than Graviton4 and is 29% cheaper billed.
> Intel finishes 9% sooner than Graviton4 and costs 5% more.

Workload, in two parts, one task per chip:

1. **The suite.** Every `siesta_subtest()` in SIESTA 5.4.2's `Tests/*/CMakeLists.txt` whose build guard
   the binary satisfies: 133 tests on Graviton4 and 134 on x86, from basis generation through spin-orbit,
   MD, TranSiesta and TDDFT. Each is checked against its committed `Reference/*.out` by the suite's own
   `yaml_compare.py`, with the suite's own `test-cfg.yml` tolerances.
2. **The dot.** `12.Solvers/si-qdot-elpa-native.fdf`, the CSIRO Si987H372 quantum dot (987 Si, 372 H,
   in a 45 Å box). Upstream's test stops it after one SCF step: the file says "For a quicker test.
   Remove if full test is desired". Here it runs (a) as that one-step test against the committed
   reference, with D&C and with MRRR, (b) with the quick-test block removed (converged SCF and forces at
   the test's SZ basis), and (c) as (b) at **DZP**, the basis a production run uses. At DZP that is
   14,691 orbitals, run twice: once with ScaLAPACK divide-and-conquer and once with ELPA through ELSI.

**Why this size.** SIESTA exists for systems with hundreds to thousands of atoms, which a localised
basis makes affordable. A 1,359-atom nanostructure, converged with forces at DZP, is a real single-point
calculation of that kind. At DZP, 95% of the wall time is the dense eigensolver (`diagon`, 1,526 of
1,600 s on Graviton4). The suite adds breadth: 133 small cases that exercise the rest of the code.

**Data tier: a durable public id, a versioned release.** The inputs are SIESTA's own repository at the
5.4.2 tag, commit `0e2722c`. [`manifest.tsv`](manifest.tsv) lists the 396 files under `Tests/` that the
suite needs (inputs, references, restart files, pseudopotentials, the comparison tools), each with its
git blob id. [`00-fetch.task.json`](00-fetch.task.json) fetches one archive of `Tests/` at that commit,
on AWS. It keeps a file only if its blob id matches the release tree. [`pins.sha256`](pins.sha256) pins
every file again at stage-in. `Pseudos/Si.psf` is byte-identical to the [recipe's](../../recipes/siesta/README.md) pin.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/siesta-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if not yet staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py siesta $a "$B" "$(date +%s)" \
    measurements/siesta-real/real.task.json)" --wait
done
bash measurements/siesta-real/collect.sh "$B"           # results/<chip>/, results/compare.txt
```

[`real.py`](real.py) is the workload and its checks. It copies the execution model of
`cmake/tests/SiestaTests.cmake` (`SIESTA_TESTS_MPI_NUMPROCS` = 4 ranks per test, the default for an
MPI build) and runs 4 tests at a time. A suite test is

```sh
SIESTA_PS_PATH=Tests/Pseudos mpiexec -n 4 siesta -out <name>_mpi4.out ../<name>.fdf
```

and the dot is `mpiexec -n 16 siesta -out qdot-dzp.out -fdf Charge.Mulliken:end qdot-dzp.fdf`.

## Results

`c8g.4xlarge`, `c8i.4xlarge` and `c8a.4xlarge` (16 vCPU, 32 GiB). The recipe's image runs on Graviton4,
and the `dft` env's linux-64 twin runs on the two x86 chips. Same staged bytes, same rank counts:

| chip | instance | tool wall | billed | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 4,506 s | 4,667 s | 22.4 GiB | 0.6381 | 0.799 | 0.827 |
| Xeon 6975P-C | `c8i.4xlarge` | 4,094 s | 4,185 s | 26.0 GiB | 0.7497 | 0.853 | 0.872 |
| **EPYC 9R45** | `c8a.4xlarge` | **2,321 s** | **2,442 s** | 26.0 GiB | 0.8622 | **0.556** | **0.585** |

A result is the whole verified job: the suite and its checks, the dot at SZ (three runs) and at DZP
(two runs). Billed is EC2 launch to termination. Peak memory is the cgroup's `memory.peak`, which
includes page cache. The largest single process was 1.84–1.85 GiB (`ru_maxrss`).

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| suite: 133–134 tests, 4 at a time × 4 ranks | 128 | 145 | 117 |
| dot, SZ: two one-step checks + the converged run | 92 | 98 | 61 |
| dot, DZP, ScaLAPACK D&C, 14 SCF steps | 1,600 | 1,644 | 1,130 |
| dot, DZP, ELSI-ELPA, 14 SCF steps | 2,677 | 2,196 | 1,005 |

- **The DZP dot is the job.** It takes 95% of the wall clock on every chip, and it is a dense
  generalised eigenproblem of order 14,691 at every SCF step. On the ScaLAPACK path AMD is 1.42×
  Graviton4, and Intel is level with Graviton4.
- **ELPA is the eigensolver where the chips differ most.** On EPYC it beats ScaLAPACK (1,005 vs 1,130 s).
  On Graviton4 it is 1.67× slower than ScaLAPACK (2,677 vs 1,600 s). Both builds use the ELPA inside
  ELSI, and this run does not show which ELPA kernel each build selected. So "ELPA is slow on arm64" is
  an observation about this build, not about ELPA.
- **AMD's hour is 35% dearer than Graviton4's**, and AMD is still 30% cheaper in compute per result and
  29% cheaper billed. Intel is 5% dearer billed than Graviton4. Intel's 16 vCPU are 8 cores × 2 threads,
  so 16 ranks share 8 cores. AMD's and Graviton4's are 16 full cores (`threads_per_core` 2 / 1 / 1 in
  `host.txt`).
- **Boot, staging and the software are 91–161 s of each bill** (billed minus tool wall): Docker and the
  image pull on Graviton4, the offline install of the env twin on x86.
- n = 1 per chip. The converged energies are fixed points and the SCF took the same number of steps on
  every chip (13 at SZ, 14 at DZP), so the work per chip is the same.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py). A failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| tests selected by the build's guards; each runs to SIESTA's end-of-run line | the suite | 133 / 133 | 134 / 134 | 134 / 134 |
| `Running on 4 nodes` in every suite test, `16 nodes` in every dot run | [rank count](../../practices/mpi-rank-count.md) | 133 + 5 | 134 + 5 | 134 + 5 |
| **every SCF-converged test passes upstream's `yaml_compare.py` against its committed reference** | **SIESTA's own references and tolerances** | **112 / 112** | **112 / 112** | **112 / 112** |
| non-zero reference values those 112 tests compare | | 1,815 | 1,815 | 1,815 |
| **dot, one step, D&C and MRRR: Etot within upstream's Etot tolerance (0.003 eV) of the committed ELPA-2 reference −110997.549364** | **published number** | **0 eV** | **−5.2e-5 eV** | **−5.2e-5 eV** |
| dot, SZ and DZP: `SCF cycle converged`, a force on each of the 1,359 atoms | completion | 3 / 3 | 3 / 3 | 3 / 3 |
| **dot: Mulliken Qtot == 4,320 valence electrons (987 × 4 + 372 × 1)** | **charge conservation** | **4320.000** | **4320.000** | **4320.000** |
| **dot, DZP: ScaLAPACK D&C and ELSI-ELPA reach the same Etot (< 0.003 eV)** | **two eigensolver libraries, one fixed point** | **0 eV** | **0 eV** | **0 eV** |

The DZP dot converges to **−112015.936544 eV** on Graviton4 and **−112015.936557 eV** on both x86 chips,
with both eigensolvers. The largest force is 0.364746 eV/Å on every chip. Two of the 112 converged suite
tests are TranSiesta's `tbtrans` steps, whose output has no SIESTA energies to compare. 110 tests carry
the 1,815 values. On Graviton4, 54 of them match their reference to every printed digit. The closest any
converged test comes to its tolerance is `04.SCFMixing/pulay`: its band-structure energy `Ebs` sits at
0.995× its tolerance on Graviton4, and its `Enl` at 0.825× on x86. Those are components, not the
variational total. `pulay`'s `Etot` is within 3e-6 eV of the reference on every chip, against 0.003.

**What is asserted is the fixed point, and SIESTA says which tests have one.** 21 tests on Graviton4
(22 on x86) stop at their `MaxSCFIterations` before convergence. They print `SCF_NOT_CONV`, and the
suite's README says the tests "use parameters which are not appropriate for a converged calculation".
Each such value is a point on the SCF path, not the answer. They are reported, not asserted:

| stopped before convergence, outside upstream's tolerance | worst term | Graviton4 | x86 (both) |
|---|---|---|---|
| `02.SpinPolarization/fe_spin` | `Eharris` | 6.0× | 9.6× |
| `02.SpinPolarization/fe_spin_directphi` | `Eharris` | 15.2× | 24.4× |
| `02.SpinPolarization/fe_noncol_gga` | `Ebs` | 29.3× | 25.4× |
| `02.SpinPolarization/fe_noncol_kp` | `Ebs` | pass (0.47×) | 1.30× |
| `02.SpinPolarization/fe_noncol_sp` | `Ebs` | 1,177× | 1,172× |
| `03.SpinOrbit/FePt-X-X` | `Ekin` | 14.5× | 33.6× |
| `04.SCFMixing/chargemix` | `Ebs` | 102× | 104× |

The other 15 unconverged tests pass. All 7 that fail are iron or iron–platinum systems stopped after
15–20 SCF steps. No converged test fails on any chip.

## Across chips: what is a fixed point and what is a path

[`compare.py`](compare.py) compares every digest value (upstream's `out_digest_yaml.awk`) of every test
both chips ran, and asserts the dot. Full output: [`results/compare.txt`](results/compare.txt).

| Graviton4 vs x86 (Intel and AMD give the same numbers) | tests | values identical to the printed digit | largest \|ΔEtot\| |
|---|---|---|---|
| converged single-point tests | 79 | 1,157–1,159 of 1,254 | 2.8e-5 eV (`ts_chain`) |
| converged trajectories (08 geometry, 09 MD, 15 TDDFT) | 33 | 499 of 561 | 1.5e-5 eV (`zm_broyden`) |
| stopped before convergence | 21 | 259 of 353 | 0.016 eV (`fe_spin_directphi`) |
| **the dot, DZP, converged** (asserted < 0.003 eV) | | | **1.3e-5 eV** |
| the dot, SZ, converged | | | 5.2e-5 eV |

**The total energy is a fixed point, and it agrees across chips.** Converged `Etot` differs by at most
2.8e-5 eV in the suite and 1.3e-5 eV for the 1,359-atom dot. That is about 1e-10 relative, and the
size of the SCF's own convergence: at `DM.Tolerance 1e-3` the last step still moves the dot's `Etot` by
5e-6 eV at DZP and 1.5e-5 eV at SZ. Energy *components* move more than the total. `pulay`'s
`Ekin` differs by 7.8e-3 eV between chips while its `Etot` agrees. The total is variational, so it is
stationary at the fixed point; its parts are not.

**The unconverged tests are a path, and they diverge.** Their `Etot` differs by up to 0.016 eV between
Graviton4 and x86, and their `Ekin` by up to 0.82 eV (`FePt-X-X`). That is also why `fe_noncol_kp` passes
on one chip and not the other. Stopping an SCF after N steps reports where the iteration was, and that
depends on every rounding on the way.

**Intel and AMD agree almost exactly**: every trajectory and every unconverged test is identical to the
printed digit, and 5 converged tests differ in the last digit (1e-6 eV). They run the same linux-64 build,
and OpenBLAS 0.3.34 selects the same `Cooperlake` kernel on both (it has no Zen 5 target). Graviton4
selects `neoversev2`. So the cross-chip differences here come from the arm64 and x86 builds and their
BLAS kernels, not from the x86 vendor. One build difference is not numeric: **the x86 SIESTA has DFT-D3
support and the arm64 one does not**. That is why x86 runs one more test (`Dependency_Tests/dftd3`).

## Caveats

- One configuration per part: 4 ranks per suite test and 16 for the dot, `OMP_NUM_THREADS=1`. No rank
  sweep. The DZP dot is 95% eigensolver, so its scaling is ScaLAPACK's or ELPA's, not SIESTA's.
- The dot's DZP basis uses the test file's other settings (`PAO.EnergyShift 300 meV`, 150 Ry mesh,
  `DM.Tolerance 1e-3`). Those are not converged production parameters for a final answer. They are
  upstream's, and the point here is a 1,359-atom, 14,691-orbital SCF run to convergence.
- The build has no native ELPA, PEXSI, Wannier90 or GPU support, so 11 upstream tests are skipped by
  their own CMake guards on every chip (`real.log` lists them). DFT-D3 is the twelfth, on arm64 only.
- The specs ran with `cost_limit` 2.5 (Graviton4) and the x86 scaling of it (3.25, 4.0), and a 5 h TTL
  as the backstop. Prices are spawn's own on-demand estimate for these instances in us-west-2.

## Spend

About **$3.3** in all. The three verifying runs cost $2.28. The rest was two fetches, two earlier
Graviton4 runs that found the run's shape, and two x86 launches stopped in their first minutes. Open MPI
5 refused 16 ranks on Intel's 8 cores until `mpiexec` was given `--map-by :OVERSUBSCRIBE`; the
environment variable the recipe sets has no effect on Open MPI 5.
