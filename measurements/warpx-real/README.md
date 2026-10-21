# WarpX on a published PIC-MCC benchmark — Graviton4, Intel and AMD

> **WarpX reproduces Turner et al. (2013) case 1 to 1.82% RMS on all three chips, and the 512,000-step
> stochastic run is byte-identical across them.** AMD is 1.19× faster than Graviton4 and 13% dearer
> per result; Intel is 2% slower and 20% dearer. More threads buy nothing for this deck: it is a single
> 128-cell grid.

Workload: the **Turner et al. (2013) capacitive-discharge benchmark, case 1**
([doi:10.1063/1.4775084](https://doi.org/10.1063/1.4775084)) — a helium RF discharge between parallel
plates 6.7 cm apart, 450 V at 13.56 MHz, electrostatic 1D PIC with Monte-Carlo collisions (elastic,
two excitations, ionisation; ion isotropic and backscatter) — at the benchmark's own resolution:
128 cells, 512 particles per cell seeded, **1,280 RF periods = 512,000 steps**, ion density averaged
over the last 32 periods. This is the problem Turner et al. set for PIC-MCC codes; five codes agreed
on it there, and WarpX ships it as `Examples/Physics_applications/capacitive_discharge`. Its CI runs
a coarse `--test` variant (32 cells, 256 per cell, 2× the time step, 320 periods). This is the full one.

## Run it

```sh
B=$(make -s print-bucket)
measurements/warpx-real/stage-inputs.sh "$B"     # deck, analysis, cross sections, scripts -> S3, sha256-checked
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py warpx $a $B r1 measurements/warpx-real/real.task.json)"
done
```

Inside the task, from a directory four levels below the cross sections (where the deck expects them),
this is upstream's command, then upstream's analysis script:

```sh
OMP_NUM_THREADS=1 python3 inputs_base_1d_picmi.py -n 1     # writes ion_density_case_1.npy
python3 analysis_1d.py                                      # asserts RMS error vs Turner < 0.065
```

## Results

One OpenMP thread on a `large` (2 vCPU) of each chip, same WarpX 26.10 (aarch.science `pic` on arm64,
its linux-64 twin `pic-bf42d7d6c3f2` on both x86 chips), same staged bytes:

| chip | instance | tool wall | billed | steps/s | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.large` | 1,228 s | 1,317 s | 417 | 129 MiB | 0.0798 | **0.0272** | **0.0292** |
| Intel (Xeon 6975P-C) | `c8i.large` | 1,259 s | 1,337 s | 407 | 136 MiB | 0.0937 | 0.0328 | 0.0348 |
| AMD (EPYC 9R45) | `c8a.large` | **1,031 s** | 1,101 s | 497 | 136 MiB | 0.1078 | 0.0309 | 0.0330 |

A *result* is the whole benchmark run; *tool wall* is WarpX alone, *billed* is launch to stage-out.
Peak RSS is `ru_maxrss` of the WarpX process.

- **Graviton4 is the cheapest per result; AMD is the fastest.** AMD finishes 1.19× sooner for 13% more
  per result; Intel is 2% slower than Graviton4 and 20% dearer. Upstream's "~20 minutes on four
  processors" is what one Graviton4 core does here.
- **Threads buy nothing, so rent the smallest box.** The deck sets `warpx_max_grid_size=128`, so the
  domain is **one grid** (`Level 0 1 grids 128 cells`), and OpenMP has nothing to divide. Measured on
  `c8g.4xlarge` over 64 RF periods: 141 s at 1 thread, 144 s at 4, 152 s at 16, with CPU use rising to
  4.9 cores — spinning, not working (`results/probe/`). Passing `amr.max_grid_size=16` on the command
  line did **not** split the grid (`results/probe2/`); splitting it means editing the deck, which is
  untested here.
- **Boot is small against the run:** 89, 78 and 70 s of launch, Docker, pull (or the 12–14 s offline
  env install on x86) and staging, around 17–21 minutes of PIC.
- **The probe overstated the rate 2.3×.** 64 periods took 141 s, which extrapolates to 47 minutes; the
  full run took 20. The early discharge holds more particles than the steady state, so the first 5% is
  the slowest 5% — sized as a ceiling, which is the safe direction, but not a representative sample.
- n = 1 per chip, defensible because the output is deterministic: byte-identical across thread
  counts and a repeat (probe), and across all three chips (below).

## Why this size

It is the benchmark itself — Turner et al. defined the grid, particle count, time step and run length,
and published the converged ion-density profile those settings produce. A smaller run is WarpX's CI
variant and a larger one is a different benchmark (cases 2–4 raise the pressure and refine to 256 and 512
cells, with no reference profile in WarpX's tree). A real CCP study is this run with a different gas, voltage
or gap, at the same cost.

## What held at scale, on all three chips

| assertion | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| deck and analysis are upstream's, at tag `26.10` | sha256; 0 lines changed | yes | yes | yes |
| OpenMP threads, from AMReX's log | == 1 launched | 1 | 1 | 1 |
| completion: final-step diagnostic | `diag1512000` written | yes | yes | yes |
| ion density | finite, positive, 129 nodes | yes | yes | yes |
| **upstream's `analysis_1d.py`**, unmodified | exit 0 (RMS < 0.065) | pass | pass | pass |
| **RMS relative error vs Turner**, interior, recomputed from the pinned script's table | < 0.065, read from that script | **0.0182** | **0.0182** | **0.0182** |
| max relative error, interior | reported | 0.1361 | 0.1361 | 0.1361 |
| peak density | reported (Turner 1.4048e14) | 1.4154e14 | same | same |
| symmetry, n(z) vs n(L−z), RMS relative | reported | 0.0085 | same | same |

**Where the error is.** The 13.6% maximum is the node next to each wall (nodes 1 and 127, −13.6% and
−13.2%), where the sheath gradient is steepest and the node-to-cell-average difference is largest;
upstream already excludes the wall nodes themselves for that reason. Excluding the next node too, the
worst point is 2.2% and the RMS 0.68%; the peak is 0.75% above Turner's and the midplane 0.23%.
Upstream's 0.065 was set for its coarse CI variant; at full resolution the run sits 3.6× inside it.
It is asserted as upstream wrote it and the observed value reported, rather than tightened to fit.

## Fixed point or path

| | Graviton4 | Intel | AMD | kind |
|---|---|---|---|---|
| time-averaged ion density, all 129 nodes, after 512,000 steps | sha256 `3c4cf95f…bfb7626` | **identical** | **identical** | path — and byte-identical |
| the same, 1 / 2 / 4 / 8 / 16 threads and a repeat (64-period probe, Graviton4) | byte-identical | — | — | path |
| `openblas_get_corename()` | `neoversev2` | `Cooperlake` | `Cooperlake` | not on this path |

- **A 512,000-step Monte-Carlo trajectory agrees to the bit on three chips and two builds.** That is
  a path, and paths usually diverge (LAMMPS by step 50). Here the random numbers come from WarpX's
  fixed default seed, the field solve is a 1D tridiagonal sweep, and no BLAS is involved — so the
  OpenBLAS kernel difference has nothing to act on. Which property of the build makes the rest of the
  arithmetic agree is not established here; the observation is that it does, at full length, on
  each chip.
- So the Turner comparison is asserted on each chip independently, and the cross-chip equality is
  **observed**, not asserted: a future build that differs in the last bit would still be correct.

## Pins

| | |
|---|---|
| deck | `Examples/Physics_applications/capacitive_discharge/inputs_base_1d_picmi.py` @ WarpX `26.10`, sha256 `5d69424f…338324` |
| reference + tolerance | `analysis_1d.py` @ `26.10` (Turner's 129-node case-1 profile, `tolerance = 0.065`), sha256 `4b63e0fe…b1f7a4` |
| cross sections | `MCC_cross_sections/He/*.dat` @ `BLAST-WarpX/warpx-data` commit `b59a35f8` (Biagi MAGBOLTZ 7.1 / Phelps), six files, sha256 in [`inputs.sha256`](inputs.sha256) |
| image | arm64 `quay.io/aarchsci/pic@sha256:e02db0b4…`; x86 the env's linux-64 twin `envs-x86_64/pic-bf42d7d6c3f2` — the recipe's pins |
| scripts | `run.sh`, `check.py`, `inputs.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes it) |

**Data tier: a versioned release.** The benchmark's reference profile is the published one (Turner et
al.'s supplementary data), committed in WarpX's tree at the matching tag; the cross sections are the
ones the deck names, from WarpX's data repository at an immutable commit. `stage-inputs.sh` streams
each from GitHub to S3 and checks the S3 copy against `inputs.sha256`; the task checks again. Raw
per-chip output is in `results/`; `probe.task.json` and `probe2.task.json` are the two sizing probes.
