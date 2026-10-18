# LAMMPS on a real workload — 2 million atoms of rhodopsin, Graviton4, Intel and AMD

> **Graviton4 is 1.37× faster than same-generation Intel and 38% cheaper per nanosecond; AMD is
> 3% faster than Graviton4 and 31% dearer per nanosecond.** All three reproduce upstream's
> committed reference energies, and the replicated system is 64 copies of the original to 4e-13.

Workload: LAMMPS' own **scaled-size** rhodopsin benchmark — `bench/in.rhodo.scaled` at
`patch_22Jul2025` (the tag matching the packaged LAMMPS), rhodopsin in a solvated lipid bilayer,
CHARMM, PPPM long-range electrostatics, SHAKE, NPT at 300 K, 2 fs steps — at the size upstream's
README assigns to 64 processors: **32,000 atoms per rank, replicated 4×4×4 to 2,048,000 atoms**,
for **5,000 steps (10 ps)**. The only change to the deck is `run 100` → `run 5000`.

## Run it

```sh
B=$(make -s print-bucket)
measurements/lammps-real/stage-inputs.sh "$B"     # the deck + scripts; data.rhodo is the recipe's own
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py lammps $a $B r2 measurements/lammps-real/real.task.json)" --wait
done
```

Inside the task, after three `run 0` checks, this is upstream's scaled-benchmark command:

```sh
mpiexec -n 64 lmp_mpi -var x 4 -var y 4 -var z 4 -in in.rhodo.scaled    # with run 5000
```

## Results

64 MPI ranks × 1 thread on a `16xlarge` (64 vCPU) of each chip, same LAMMPS 22 Jul 2025 Update 6
(aarch.science `md` arm64; its linux-64 twin on both x86 chips), same staged bytes:

| chip | instance | tool wall | billed | ns/day | peak mem | $/hr | compute $/result | billed $/result | **$/ns** |
|---|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `c8g.16xlarge` | **1,026 s** | 1,185 s | 0.845 | 8.2 GiB | 2.5523 | **0.727** | **0.840** | **72.7** |
| Intel (Xeon 6975P-C) | `c8i.16xlarge` | 1,408 s | 1,546 s | 0.615 | 13.8 GiB | 2.9987 | 1.173 | 1.288 | 117.3 |
| AMD (EPYC 9R45) | `c8a.16xlarge` | 997 s | 1,122 s | 0.869 | 13.8 GiB | 3.4486 | 0.955 | 1.075 | 95.5 |

A *result* is the whole 10 ps run; *tool wall* is the production `lmp_mpi` alone, *billed* is
launch to terminate; **$/ns** is compute dollars per simulated nanosecond.

- **Graviton4 vs Intel: 1.37× faster, 38% cheaper per ns.** Per vCPU, which is what is rented:
  `c8g` and `c8a` vCPUs are full cores (`Thread(s) per core: 1`), `c8i`'s are 32 cores × 2
  hyperthreads, so 64 ranks on Intel share cores pairwise. That is the like-for-like rental, not a
  per-core comparison.
- **AMD vs Graviton4: 1.03× faster, 31% dearer per ns.** The c8a hour costs 35% more than c8g's
  for 3% more throughput.
- **The step rate is a sawtooth with a 1,000-step period** — LAMMPS re-sorts atoms spatially every
  1,000 steps (`atom_modify sort` default), and the rate decays ~13% between sorts as locality
  drifts. Per 1,000-step window on Graviton4: 4.99, 4.88, 4.86, 4.86, 4.85 steps/s. So the steady
  state is reached after the first window, and a 1,000-step run (0.862 / 0.625 / 0.891 ns/day,
  the first run on each chip) overstates it by 1.6–2.5%. Size from whole sort periods.
- **LAMMPS' own memory estimate is identical on all three**, 142.2 MB per rank. The cgroup peak
  above is higher on x86 because that route installs the env into the container at start, and the
  cgroup counts its page cache.
- **Compute-dominated:** 125–159 s of boot, Docker, image pull (or env install on x86), staging
  and the three `run 0` checks, around 17–23 minutes of MD.
- Prices are spawn's printed on-demand us-west-2 rates. n = 2 per chip (a 1,000-step and a
  5,000-step run); each chip's two trajectories are identical to every printed digit through step
  1,000, so the timings are of one deterministic computation per chip.

## Why this size

It is upstream's own definition of the scaled benchmark at this core count (32,000 atoms per
processor, `bench/README`), and 2 million atoms is a large but ordinary membrane-protein system —
the [recipe](../../recipes/lammps/README.md)'s 128,000 atoms on 16 ranks is the fixed-size case.
MD cost is linear in steps once the rate is steady, so 10 ps (five sort periods) gives the
production rate; a 100 ns campaign is the same computation 10,000 times longer, at the $/ns above.

## What held at scale, on all three chips

| assertion | bound | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| MPI ranks, from LAMMPS' log | == 64 launched | 64 | 64 | 64 |
| atoms; bonds / angles / dihedrals / impropers | exactly 64 × the 32,000-atom system's | 2,048,000; 1,774,272 / 2,589,888 / 3,637,056 / 66,176 | same | same |
| **upstream's committed reference** (`bench/log.15Jul25.rhodo.fixed.g++.1`, step 0): every energy term, pressure, volume, and PPPM's `G = 0.24883488`, `grid = 25 32 32` | ≤ 1e-4, one unit in the reference's last printed digit | worst 2.9e-5 | worst 2.9e-5 | worst 2.9e-5 |
| **extensivity**: 4×4×4 replica at the same `g_ewald` and a 4× mesh (`100 128 128`), every term incl. `E_long` | relative ≤ 1e-10 of 64 × the base | **4.48e-13** | **4.50e-13** | **4.50e-13** |
| production step 0: KE, bonded and vdW terms, volume / 64 | ≤ 1e-4 of upstream's reference | holds | holds | holds |
| completion; NPT holds 300 K | `Total wall time` printed; final T 280–330 K | 300.19 K | 299.90 K | 299.87 K |

**Why extensivity is exact and why its bound is 1e-10.** A periodic cell replicated 4×4×4 is the
same infinite lattice, so every term is exactly 64× the original *provided* the Ewald split is the
same — same `g_ewald`, and a PPPM mesh whose spacing is unchanged, so the replica's charge density
transforms onto exactly the original's k-vectors. What remains is double-precision summation order
over ~10⁹ pair terms; 1e-10 is 200× the observed 4.5e-13 and far below any physical
error. Left to choose for itself (the production deck), PPPM picks `G = 0.24521748`, `grid = 96 125
120` for the big box, which moves the real/reciprocal split by ±3,918 kcal/mol per cell and their
sum by **0.605** of 46,801 kcal/mol (1.3e-5, inside PPPM's requested 1e-4) — reported, not asserted.
The Ewald-independent terms are asserted on the production run itself.

## Fixed point or path

| | Graviton4 | Intel | AMD | kind |
|---|---|---|---|---|
| step 0 of the production run, every term (13 values to 4 decimals) | −1622758.4228 TotEng | identical | identical | single evaluation: identical |
| base system at full precision, `E_coul` | 207025.89337112**99** | 207025.89337113**58** | same as Intel | single evaluation: 3e-14 relative |
| TotEng at step 50 / 500 | −1621042.3350 / −1607686.2832 | …3349 / −1607672.2604 | …3348 / −1607606.4131 | path: diverges in the 11th figure by step 50 |
| final T, TotEng at 5,000 | 300.19 K, −1614292.29 | 299.90 K, −1615410.71 | 299.87 K, −1615133.99 | path, reported |
| neighbour-list builds | 662 | 655 | 659 | path |

- **Every fixed-point claim agrees across chips; every trajectory differs, and each differs
  deterministically.** The two x86 chips run the *same binary* and still diverge from each other
  as much as from Graviton4, while each chip reproduces its own trajectory exactly across two runs
  on two instances. So the divergence is chip-dependent code selection under one binary
  (runtime-dispatched math or FFT kernels), not run-to-run noise; which library is not established
  here. LAMMPS reports `FFT engine = mpiFFT` on all three; the env's arm64 OpenBLAS reports
  `neoversev2`, and the x86 twin carries MKL instead.
- So assert energies at a single evaluation, and compare trajectories only as distributions
  (temperature, density), never value by value.

## Pins

| | |
|---|---|
| system | `bench/data.rhodo` @ `patch_22Jul2025`, sha256 `9b14e259…f23863` (the recipe's staged copy, reused) |
| deck | `bench/in.rhodo.scaled` @ `patch_22Jul2025`, sha256 `000d5cc5…fd8cce`; both re-checked in-task (`inputs.sha256`) |
| reference | `bench/log.15Jul25.rhodo.fixed.g++.1` @ `patch_22Jul2025` (sha256 `01384b04…7ea528`), step-0 values embedded in `check.py` |
| image | arm64 `quay.io/aarchsci/md@sha256:28537712…`; x86 the env's linux-64 twin `envs-x86_64/md-f7ed8789d24e` — the recipe's pins |
| scripts | `run.sh`, `check.py`, `inputs.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes it) |

**Data tier: a versioned release.** No RODA or DOI-archived LAMMPS input exists for a biomolecular
system; the benchmark set ships in the LAMMPS repository at each release tag, with reference logs
beside it. `real-1k.task.json` is the 1,000-step run (output prefix `r1`); raw per-chip output for
both lengths is in `results/`.

**Open MPI needs its shared memory moved at this rank count.** Docker gives the container a 64 MB
`/dev/shm`; at 64 ranks Open MPI's shared-memory segments do not fit and the first collective dies
with SIGBUS (exit 135). `run.sh` sets `OMPI_MCA_btl_sm_backing_directory=/tmp`.
