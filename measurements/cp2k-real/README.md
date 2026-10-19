# CP2K on its own 64-water benchmarks — Graviton4, Intel and AMD

> **CP2K 2026.2 runs ten production-setting ab-initio MD jobs on liquid water on all three chips and
> reproduces the output its developers committed for them.** For four of the five functionals, all
> 12 printed digits match on every chip. The fifth is hybrid PBE0. There Graviton4 is within
> 4e-8 Ha of the reference and both x86 chips within 1.6e-7. CP2K's own release check also passes on
> every chip: five MPI × OpenMP layouts of the same 16 vCPU agree within 3e-12 Ha. AMD EPYC runs the
> set 1.8× faster than Graviton4 and is 25% cheaper. Intel takes as long as Graviton4 and costs 18% more.

Workload: [`benchmarks/`](https://github.com/cp2k/cp2k/tree/v2026.2/benchmarks) at tag **v2026.2**,
the version in the image.

- **`QS_reference/w64{PBE,SCAN,PBE0,GAPW,ccGRB}.inp`**: 64 H2O (192 atoms) in a 12.4 Å box. Each runs
  10 Born–Oppenheimer MD steps from an atomic guess, with SCF to `EPS_SCF 1e-7`. The functionals are
  GGA, meta-GGA, hybrid with ADMM, all-electron GAPW, and the ccGRB basis. Upstream calls these
  *"realistic parameter settings"*. Each input ships with a `.ref`, the output of the developers' own
  run: CP2K 2023.2 on 48 ranks of an EPYC 7401 (2026.1 at 48×2 for ccGRB).
- **`QS/H2O-64.inp`**: the classic H2O-64 benchmark (TZV2P, 280 Ry). It runs under five
  decompositions (16×1, 8×2, 4×4, 2×8, 1×16 ranks × threads). This is the check that
  [`check-release-comparison.py`](https://github.com/cp2k/cp2k/blob/v2026.2/benchmarks/QS/check-release-comparison.py)
  makes on a release.

**Why this size:** 64 waters is the standard production cell for liquid-water AIMD. These are
CP2K's own benchmark inputs, at the settings people publish with. A production run is the same step
repeated 10⁴–10⁵ times, so 10 steps measures the per-step cost a researcher pays. The sixth input,
DC-SCAN, is skipped: it costs 30× PBE (3,525 s on upstream's 48 cores).

**Data tier: a durable public id, a versioned release.** [`manifest.tsv`](manifest.tsv) lists the 14
files (113 KB) with their git blob ids in the v2026.2 tree. [`stage-inputs.sh`](stage-inputs.sh)
keeps a file only if its blob id matches. [`pins.sha256`](pins.sha256) pins the bytes again at every
stage-in.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/cp2k-real/stage-inputs.sh "$B"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py cp2k $a "$B" "$(date +%s)" \
    measurements/cp2k-real/real.task.json)" --wait
done
bash measurements/cp2k-real/collect.sh "$B"      # results/<chip>/, results/compare.txt
```

[`real.py`](real.py) runs each job as `mpiexec -n R cp2k.psmp -i <input>` with `OMP_NUM_THREADS=T`, in
its own directory on the root disk. It then makes the checks below.

## Results

`m8g.4xlarge`, `m8i.4xlarge` and `m8a.4xlarge` (16 vCPU, 64 GiB). Graviton4 runs the recipe's image.
Both x86 chips run the `cp2k` env's linux-64 twin. All three read the same staged bytes and run the same
ten jobs:

| chip | instance | tool wall | billed | peak memory | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 4,032 s | 4,186 s | 15.1 GiB | 0.7181 | 0.804 | 0.835 |
| Xeon 6975P-C | `m8i.4xlarge` | 4,072 s | 4,185 s | 18.9 GiB | 0.8467 | 0.958 | 0.984 |
| **EPYC 9R45** | `m8a.4xlarge` | **2,218 s** | 2,322 s | 18.9 GiB | 0.9738 | **0.600** | **0.628** |

A result is the whole verified set: ten MD runs of 10 steps. *Billed* runs from instance launch to
termination. Peak memory is the container cgroup's `memory.peak`, all ranks together. It includes
page cache, and on x86 the env install. The largest single process was 2.1 GiB (H2O-64 at 1×16,
`ru_maxrss`).

| job (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| w64 PBE | 195 | 160 | 84 |
| w64 SCAN | 359 | 393 | 222 |
| w64 PBE0 (ADMM) | 987 | 1,185 | 628 |
| w64 GAPW (all-electron) | 1,159 | 1,069 | 598 |
| w64 ccGRB | 974 | 920 | 485 |
| H2O-64, 16×1 / 8×2 / 4×4 / 2×8 / 1×16 | 60 / 64 / 68 / 73 / 94 | 57 / 61 / 66 / 72 / 90 | 31 / 40 / 39 / 37 / 55 |

- **AMD is 1.82× Graviton4 on the whole set**, from 1.6× on SCAN to 2.3× on PBE. Its hour costs 36%
  more, and it is still 25% cheaper per result.
- **Intel's 16 vCPU are 8 cores × 2 threads, and they tie Graviton4's 16 cores** (1.01×). Intel is
  quicker on PBE, GAPW and ccGRB and slower on SCAN and PBE0. It costs 18% more per result billed.
  AMD's and Graviton4's vCPUs are full cores (`threads_per_core` 1).
- **Pure MPI is the fastest layout on every chip.** 1×16 (all OpenMP) is 1.6–1.8× slower than
  16×1 on H2O-64.
- **Peak memory was under 19 GiB, so `c8g.4xlarge` (32 GiB, $0.638/hr) would have held it.** The
  `m8` boxes were picked before GAPW's footprint was measured.
- **Overhead is 104–154 s of each bill** (boot, image pull or env install). That is under 5% here.
- n = 1 per chip. The whole set agrees across chips at the 1e-9 Ha level or better, apart from
  PBE0 (see below), so the work done per chip is the same.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.py`](real.py). A failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| exit 0 and `PROGRAM ENDED`, every job | completion sentinel | 10 / 10 | 10 / 10 | 10 / 10 |
| **MPI processes and OpenMP threads CP2K reports == launched** | [rank-count guard](../../practices/mpi-rank-count.md) | **10 / 10** | **10 / 10** | **10 / 10** |
| 11 energies (steps 0–10) and an 11-row `.ener` per job | the MD ran its 10 steps | 10 / 10 | 10 / 10 | 10 / 10 |
| every SCF converged, reference set | what makes step 0 a fixed point | 0 failures | 0 | 0 |
| **H2O-64: every layout's final energy within 1e-10 Ha of the mean** | **CP2K's release check** | **2.7e-12** | **3.0e-12** | **3.0e-12** |

And across chips, asserted by [`compare.py`](compare.py):

| claim | Graviton4 vs Xeon | Graviton4 vs EPYC | Xeon vs EPYC |
|---|---|---|---|
| **step 0 of each reference job, an SCF converged at the same geometry: \|Δ\| ≤ 1e-9 Ha** | **7.6e-10** | **7.6e-10** | **3.4e-10** |

The 1e-9 Ha bound is the recipe's own clause: 1 kcal/mol is 1.6e-3 Ha. The worst case is PBE0,
Graviton4 against either x86 chip, at 0.76 of the bound. GAPW is next at ≤3.4e-10. PBE, SCAN and
ccGRB agree to ≤7e-13. PBE0's exchange is screened (Schwarz) and ADMM-fitted, so its SCF lands slightly less sharply
than a GGA's. That is the headroom to watch if this bound ever fails.

**Against the developers' committed output (reported, not asserted).** The `.ref` prints 12 digits,
about 1e-8 Ha. It came from another version (2023.2), another CPU and 48 ranks, after 10 MD steps:

| | final potential energy | trajectory average | final conserved quantity |
|---|---|---|---|
| PBE, SCAN, GAPW, ccGRB | all 12 digits, all chips | all 12 digits, all chips | all 12 digits, all chips |
| PBE0, Graviton4 | 12 digits | 1e-8 | 4e-8 |
| PBE0, Xeon and EPYC | −1.6e-7 | −3e-8 | −9e-8 |

The full table is [`results/<chip>/vs-reference.tsv`](results/arm64/vs-reference.tsv).

## Across chips: what is a fixed point and what is a path

**Step 0 is a fixed point**, and it agrees to ≤7.6e-10 (above). **Steps 1–10 are a path**: the atoms
move on forces from the previous SCF, and each new SCF starts from an extrapolated wavefunction.
That path still holds to 12 digits on four functionals across three chips, two builds and three
years of CP2K. PBE0 is the one that drifts. Graviton4 and the x86 chips end **1.6e-7 Ha** apart, while
Xeon and EPYC end within **4e-10** of each other. Both x86 chips run the same linux-64 build and the
same OpenBLAS kernel (`Cooperlake`; Graviton4's is `neoversev2`). So the PBE0 divergence comes from the
build and the BLAS kernel, not the vendor. Over 5 fs it is 1e-4 kcal/mol.

**H2O-64's step 0 is a path too.** The input sets `IGNORE_CONVERGENCE_FAILURE`, and its first SCF
stops unconverged at 50 iterations, so different layouts and chips stop at different iterates
(3.2e-9 Ha apart at most, over all 15 runs). The later steps converge, and by step 10 all 15 are
within 7.0e-12 of each other. Upstream's release check reads the final energy. That is the right
choice: the final energy is converged, step 0 is not. The 1e-10 criterion has ~30× headroom on all three chips.

## Caveats

- **Upstream's release-check script cannot read 2026.2's output.** Its pattern
  `ENERGY\| Total FORCE_EVAL.+?:` needs a colon. The 2026.2 line,
  `ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]   -1101.031…`, has none, so the script matches
  nothing. [`real.py`](real.py) uses the same criterion with a pattern that takes the number after the
  unit, and says so.
- One functional set, 10 steps each. DC-SCAN (30× PBE) is not run. Nothing here is a scaling claim
  beyond one 16-vCPU box. A production trajectory's drift and statistics are not tested, only its first
  10 steps.
- `--bind-to none` on every chip, so the scheduler placed the ranks. Binding is the obvious next lever
  and is not measured here.
