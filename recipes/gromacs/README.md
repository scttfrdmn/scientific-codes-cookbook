---
tool: gromacs
tool_version: 2026.3
env: md
image: quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f
env_x86_64: md-f7ed8789d24e
spawn_version: 0.130.1
last_verified: 2026-10-17
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# GROMACS — benchMEM at 82k atoms, in ns/day across four Graviton generations

Runs the standard benchMEM benchmark (81,743 atoms, PME, NPT) as shipped and reports ns/day and $/ns. For anyone sizing an MD run on ARM.

> **Graviton5 is 2.43× Graviton2 on this system and 47% cheaper per nanosecond** — the biggest generational gain measured anywhere in this catalog, on a NEON build with no SVE.

## Run it

```bash
make stage RECIPE=gromacs   # once: the pinned benchMEM.tpr
spawn task run --spec "$(make -s spec RECIPE=gromacs)" --wait   # 10,000 steps (20 ps), ~70 s, self-terminating
make ls    RECIPE=gromacs   # smoke-check.txt + bm.log

mpiexec -n 16 gmx_mpi mdrun -s benchMEM.tpr -deffnm bm -ntomp 1 -nb cpu -pin off
```

## Which box — [measured](../../measurements/gromacs-real/README.md) (same tpr, same digest, 16 cores, 16×1)

| generation | instance | **ns/day** | mdrun wall | $/hr | **$/ns** |
|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | 14.691 | 121 s | 0.5440 | 0.8887 |
| Graviton3 | `c7g.4xlarge` | 23.052 | 78 s | 0.5800 | 0.6039 |
| Graviton4 | `c8g.4xlarge` | 25.983 | 69 s | 0.6381 | 0.5894 |
| **Graviton5** | `c9g.4xlarge` | **35.741** | **50 s** | 0.6955 | **0.4670** |

The ladder was measured on the previous `md` digest (`1ee941664add…`); the repinned image re-ran Graviton4 only, at 26.099 ns/day.

**Take the newest generation** — every step is faster *and* cheaper per nanosecond, and the ladder is uneven: Graviton3→4 buys 12.7%, **Graviton4→5 buys 37.6%**. For contrast the integer-heavy genomics recipes gain 1.84–2.0× across these same chips against MD's 2.43×: one data point that FP-heavy codes benefit more, not yet a rule.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| benchMEM (82k atoms) | your own `.tpr` | a `.tpr` is self-contained, so nothing else needs staging; GROMACS 2026.3 still reads benchMEM's 2015-era file. |
| `-n 16 -ntomp 1` | any rank×thread split | **barely matters here** — five splits of 16 cores spanned 9.6%. Don't tune it before you measure it. |
| 16 cores | more cores, or `benchRIB` (2M atoms) | 82k atoms is small enough that PME limits scaling; a bigger system is how you use a bigger box. |

**Leave the workload** — benchMEM as shipped is what makes ns/day comparable to published numbers. **Scale it** by moving to a larger benchmark before adding cores.

<details>
<summary>As shipped: the checks, why MD gets no exact assertion, the decomposition sweep, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| steps completed | 10001 (the tpr's own 10,000) | **10001** |
| **MPI ranks used** | **== ranks launched (16)** | **16** |
| OpenMP threads per rank | == requested (1) | **1** |
| SIMD path | recorded | **ARM_NEON_ASIMD** |
| mean temperature | 280–330 K | **301.998 K** |
| atoms in `confout.gro` | 81,743 — none lost | **81,743** |
| ns/day | > 0, recorded | **26.099** |

**The rank-count assertion is the load-bearing one.** conda-forge ships nompi GROMACS builds at
higher build numbers than the openmpi ones, so an unpinned solve can hand back a serial binary that
under `mpiexec -n 16` runs sixteen independent rank-0 simulations — each printing plausible physics
and a plausible ns/day. Reading back `Using 16 MPI processes` from GROMACS' own log is what proves
the parallelism happened ([the practice](../../practices/mpi-rank-count.md)).

### Why there is no exact energy assertion

The old version of this recipe asserted a potential energy exactly, which worked because it ran 20
steps of 216 waters with no velocity generation. That does not survive contact with a real system:
**two runs of this tpr at the same 16×1 decomposition gave mean temperatures of 300.127 K and
301.998 K**, and potentials 0.28% apart. MD at 82k atoms with PME and a Berendsen thermostat is not
bit-reproducible run to run, so an exact assertion would be flaky — the
[stochastic-search rule](../../practices/cross-checks.md) one domain over. What is asserted instead
is a physically meaningful band on temperature plus the structural invariants above.

### The decomposition sweep — measured, and it barely matters

All five splits of 16 cores, one `c8g.4xlarge`, one task, so instance variance cannot contaminate
the comparison:

| ranks × threads | ns/day |
|---|---|
| **16 × 1** | **25.538** |
| 1 × 16 | 25.432 |
| 8 × 2 | 24.860 |
| 4 × 4 | 24.168 |
| 2 × 8 | 23.292 |

**9.6% from best to worst, and all five work.** Pure MPI and pure OpenMP land within 0.4% of each
other. So on a single node at this size, decomposition is not the lever it is often assumed to be —
which is worth knowing before spending an afternoon on `-npme` and pinning. That conclusion is
scoped to one node and 82k atoms; a system big enough to need several nodes, where PME and
communication start to dominate, is a different question this recipe does not answer.

### Pins

| | data tier |
|---|---|
| GROMACS | `quay.io/aarchsci/md@sha256:285377124854…` (2026.3-conda_forge, `linux/arm64`, NEON build) |
| benchMEM | `https://www.mpinat.mpg.de/benchMEM` — zip sha256 `3c1c8cd4f274…`, tpr sha256 `5099268bf3a3…` |

**Repinned 2026-10-17** from `md@sha256:1ee941664add…`, whose env can no longer be re-solved:
conda-forge later capped its mdtraj 1.11.1 at `numpy <2.5` while the image ships numpy 2.5.2
([why that matters](../../practices/environment-durability.md)). GROMACS is 2026.3 on both digests.

The benchmark set is published by the Dept. of Theoretical and Computational Biophysics, Max Planck
Institute for Multidisciplinary Sciences, Göttingen, under CC-BY 4.0, and is the set used in
[Kutzner et al.](https://doi.org/10.1002/jcc.24030) — which is why ns/day here is comparable to a
large published literature. The download is a zip despite its `.tpr` name; the pinned hash covers
both.

**The binary is a NEON build** — `ARM_NEON_ASIMD`, read back from the run rather than assumed. So
these numbers are what the packaged build delivers, which is what a cookbook owes, and not
necessarily GROMACS' ceiling on ARM. Before assuming an SVE build would help, see
[the SIMD-width measurements](../../measurements/simd-width/README.md): on these cores SVE and NEON
issue the same bits per cycle, and the win comes from `-mcpu`, not from selecting SVE.

### Run + verify

```sh
make run RECIPE=gromacs
make ls  RECIPE=gromacs
```

Expect `smoke-check.txt` with `ranks_used 16`, `simd ARM_NEON_ASIMD`, `confout_atoms 81743` and
`ns_per_day` near 26 on `c8g.4xlarge`.

### x86_64

Verified on `c8i.4xlarge` (2026-10-17) from the env's linux-64 twin (`envs-x86_64/md-f7ed8789d24e`,
228 of 245 versions identical, GROMACS 2026.3 `mpi_openmpi`). Its BLAS is **MKL** where arm64's is
OpenBLAS (the lock header records why). The box is 8 cores / 16 hardware threads, so the 16 ranks
land on hyperthreads; `--oversubscribe`, already in the launch, is what lets Open MPI 5 place them.

| | Graviton4 (`c8g.4xlarge`) | x86 (`c8i.4xlarge`) | kind |
|---|---|---|---|
| ranks used | 16 | 16 | asserted, holds |
| steps / atoms out | 10001 / 81743 | 10001 / 81743 | exact, identical |
| SIMD | `ARM_NEON_ASIMD` | `AVX2_256` (host has `AVX_512`) | build |
| mean T / mean Epot | 301.998 K / −1.28782e+06 | 301.998 K / −1.28782e+06 | path; equal to the printed 6 figures |
| ns/day | 26.1 | 29.4 | timing, not compute cost |

conda-forge's x86 GROMACS is compiled for `AVX2_256`, and `bm.log` notes that `AVX_512` was
available, so the x86 number is not GROMACS' best on that chip.

</details>
