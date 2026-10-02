---
tool: lammps
tool_version: 2025.07.22
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.111.4
last_verified: 2026-09-30
---
# LAMMPS — rhodopsin at 128k atoms, in ns/day across four Graviton generations

Runs LAMMPS' own rhodopsin benchmark (CHARMM, PPPM, NPT) replicated to 128,000 atoms and reports ns/day and $/ns. For anyone sizing a biomolecular MD run on ARM.

> **Graviton5 is 2.24× Graviton2 here and 43% cheaper per nanosecond.** Close to [GROMACS' 2.43×](../gromacs/README.md) on a comparable system — two MD codes agreeing that this is roughly what MD gains across these chips.

## Run it

```bash
make stage RECIPE=lammps   # once: the pinned data.rhodo (32,000-atom system)
spawn task run --spec "$(make -s spec RECIPE=lammps)" --wait   # 1000 steps at 128k atoms, ~48 s, self-terminating
make ls    RECIPE=lammps   # smoke-check.txt + lmp.log

mpiexec -n 16 lmp_mpi -in in.rhodo -log lmp.log     # replicate 2 2 1 → 128k atoms
```

## Which box — measured (same deck, same digest, 16 MPI ranks)

| generation | instance | **ns/day** | loop time | atom-step/s | **$/ns** |
|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | 1.978 | 87.38 s | 1.465 M | 6.601 |
| Graviton3 | `c7g.4xlarge` | 2.942 | 58.73 s | 2.179 M | 4.731 |
| Graviton4 | `c8g.4xlarge` | 3.635 | 47.54 s | 2.692 M | 4.213 |
| **Graviton5** | `c9g.4xlarge` | **4.425** | **39.05 s** | **3.278 M** | **3.772** |

**Take the newest generation** — faster and cheaper per nanosecond at every step. Unlike GROMACS,
the ladder here is fairly even (49%, 24%, 22%), so there is no single generation that suddenly pays
for itself.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| rhodopsin, `replicate 2 2 1` | your own data file, or a larger replicate | the deck is LAMMPS' `bench/in.rhodo` with two changes (below); replication is how the benchmark set is meant to be scaled. |
| `-n 16` pure MPI | fewer ranks, or add OpenMP | LAMMPS' default build here is MPI-only; [assert the rank count](../../practices/mpi-rank-count.md) whatever you choose. |
| `pppm 1e-4` | your accuracy target | PPPM is the long-range solver and usually the first thing to limit scaling on a small system. |

**Leave the workload** — a real biomolecular system with long-range electrostatics at production
size, so ns/day transfers. **Scale it** by replicating further before adding cores.

<details>
<summary>As shipped: the checks, the two deck changes, what the cross-code comparison does and does not say, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| atoms | **128,000** — 32,000 replicated 2×2×1 | **128,000** |
| steps | 1000 | **1000** |
| **MPI ranks used** | **== ranks launched (16)** | **16** |
| final temperature | 280–330 K (NPT at 300 K) | **299.73 K** |
| ns/day | > 0, recorded | **3.635** |

**The rank-count assertion is the load-bearing one.** conda-forge ships nompi builds at higher build
numbers than the openmpi ones, so an unpinned solve can hand back a serial binary that under
`mpiexec -n 16` runs sixteen independent single-rank simulations — each printing plausible physics
*and* a plausible ns/day. Reading `16 MPI tasks` back from LAMMPS' own log is what proves the
parallelism happened.

No exact energy is asserted. This is NPT with SHAKE and PPPM at 128k atoms; it is not
bit-reproducible run to run, so an exact value would be flaky — the same reason the
[GROMACS recipe](../gromacs/README.md) stopped asserting one when it moved to a real system.

One unit trap worth knowing: **LAMMPS switches the atom-step unit with magnitude**, printing
`katom-step/s` on small systems and `Matom-step/s` here. Capturing the number without its unit is a
1000× mislabel, so the check records both.

### The deck: `bench/in.rhodo` plus exactly two changes

The input is LAMMPS' own benchmark deck at tag `patch_22Jul2025`, matching the packaged version, with
two documented deviations:

- `replicate 2 2 1` — 32,000 atoms → **128,000**, so the run is a production-sized system rather
  than a tuning fixture. Replication is how this benchmark set is designed to be scaled.
- `run 100` → `run 1000`, so the timed section is ~40–90 s rather than a few seconds, which is what
  makes the generation comparison mean anything.

Everything else — CHARMM force field, `lj/charmm/coul/long`, `pppm 1e-4`, SHAKE, NPT at 300 K, 2 fs
timestep — is upstream's. The deck is embedded in the task spec rather than staged, so it lives in
git where the two changes are visible in diff.

### What the GROMACS comparison does and does not say

LAMMPS gains **2.24×** from Graviton2 to Graviton5; GROMACS gains **2.43×** on benchMEM. Those
ratios are comparable because each code is measured against *itself* across chips. The absolute
ns/day figures are **not** comparable — different force fields, different systems, different
long-range solvers — and reading 4.425 against GROMACS' 35.741 as a performance verdict would be
meaningless.

What the pair does support is a modest, quantified version of a claim worth being careful about:
across these four chips the two MD codes gain **2.24–2.43×** while the integer-and-string-heavy
genomics recipes here gain **1.84–2.00×** ([bwa](../bwa-samtools/README.md),
[salmon](../salmon/README.md), [gatk4](../gatk4/README.md)). So FP-heavy codes do benefit more, by
roughly 15–25% — a real effect, and a much smaller one than "FP-heavy codes benefit dramatically
more" would imply. Two codes per side is not a survey.

### Pins

| | data tier |
|---|---|
| LAMMPS | `quay.io/aarchsci/md@sha256:1ee94166…` (2025.07.22, `linux/arm64`) |
| deck | `bench/in.rhodo` @ `patch_22Jul2025`, sha256 `5599f0388a36…` (upstream, before the two changes) |
| system | `bench/data.rhodo` @ `patch_22Jul2025`, sha256 `9b14e259b99b…` — verified on the box each run |

The tag matches the packaged LAMMPS version, which matters because a benchmark deck from another
release is a different workload. `data.rhodo` is staged (6.3 MB) and its hash re-checked inside the
task before the run, so a silently changed input fails the task rather than the science.

### Run + verify

```sh
make run RECIPE=lammps
make ls  RECIPE=lammps
```

Expect `smoke-check.txt` with `atoms 128000`, `ranks_used 16`, `final_temp_K` near 300 and
`ns_per_day` near 3.6 on `c8g.4xlarge`.

</details>
