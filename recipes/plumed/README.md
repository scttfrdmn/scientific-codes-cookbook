---
tool: plumed
tool_version: 2.9.2
env: md
image: quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f
env_x86_64: md-f7ed8789d24e
spawn_version: 0.131.0
last_verified: 2026-10-17
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# PLUMED — collective variables from a live 23k-atom GROMACS run

Couples PLUMED to 100 ps of GROMACS on 23,262 atoms and reads back CVs that match the force field exactly. For anyone adding CVs or biasing to an MD run.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=plumed)" --wait   # ~4 min on c8g.2xlarge
make ls RECIPE=plumed   # COLVAR + smoke-check.txt + every GROMACS log
```

```bash
# PLUMED is coupled live, every step -- not run on a saved trajectory
gmx_mpi mdrun -s t.tpr -deffnm out -plumed plumed.dat -ntomp 8 -pin on
```

```text
d: DISTANCE ATOMS=1,2            # rigid water geometry: a force-field constant
a: ANGLE    ATOMS=2,1,3
c: COORDINATION GROUPA=1,4,7,10,13 GROUPB=<all 7754 O> R_0=0.35
PRINT ARG=d,a,c FILE=COLVAR STRIDE=100
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 7,754-water box | your own system | nothing is staged — spc216 and tip3p ship in the GROMACS package. |
| `DISTANCE` / `ANGLE` / `COORDINATION` | `METAD`, `RESTRAINT`, any CV | the coupling is what this recipe proves; the CV is yours to choose. |
| `STRIDE=100` | every step | PLUMED evaluates its CVs every step regardless; `STRIDE` only thins the output. |
| live coupling | `plumed driver` on saved trajectories | run `plumed --no-mpi driver`: this MPI build's `MPI_Init` costs 1.06 s per call, same COLVAR ([measured](../../measurements/plumed-real/README.md)). |

**Leave the system** — 23k atoms with PLUMED in the loop is a realistic coupling cost. **Scale it**
by CV expense: a `COORDINATION` over every pair is O(N²) per step and will dominate GROMACS.

## Shape, size, cost

`c8g.2xlarge`: minimisation + **100 ps of coupled NVT in 153 s**, ~$0.02 ([GROMACS's own scaling](../gromacs/README.md)).
**Driver at scale, [measured](../../measurements/plumed-real/README.md):** 16,000 RODA trajectories, 174 M CVs equal to
numpy and cpptraj: **$0.158** compute on `c8g.4xlarge`, $0.190 `c8i`, $0.211 `c8a`; walls tie, so price decides.

<details>
<summary>As shipped: CVs checked against force-field constants, and one observation deliberately not asserted</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| coupling ran | GROMACS finished **and** PLUMED wrote CVs | **501 rows, mdrun finished** |
| COLVAR rows | exactly 501 | **501** |
| **O-H distance** | **0.09572 nm** (TIP3P, via PLUMED `DISTANCE`) | **0.09572** |
| **H-O-H angle** | **1.82422 rad** = 104.52° (via `ANGLE`) | **1.82422** |
| distance invariant | spread < 1e-4 nm over 501 frames | **0.0e+00** |
| angle invariant | spread < 1e-3 rad | **7.0e-06** |

**These are force-field constants, not sampled values.** `constraints = h-bonds` holds every water
rigid, so TIP3P's O-H bond *is* 0.09572 nm and its angle *is* 104.52° — there is no tolerance to
choose, only the force field's own numbers. PLUMED reading anything else would mean the
GROMACS↔PLUMED coupling, the atom indexing, or the CV machinery is wrong. That makes this a
constraint check rather than a band, which is why it survives at any system size or run length.

The invariance checks are the other half: a rigid geometry must not move across 501 frames of real
dynamics. One run gave a distance spread of exactly `0.00e+00` and the next `2.00e-06` nm — both
pass, and the difference is where constraint round-off lands in a different trajectory, which is why
this is a bound and not an equality.

### The coordination number is reported, not asserted

| | value |
|---|---|
| `COORDINATION` total | **40.801** (sum over 5 central O × all O pairs) |
| per central atom | **8.160** within R_0 = 0.35 nm |
| per-atom range | 7.331 … 8.950 |

Two reasons it is an observation. PLUMED's `COORDINATION` with `GROUPA`/`GROUPB` sums over **all
pairs**, so with 5 central oxygens the CV is a total — an earlier version of this recipe printed it
as "per O" and read 40.686, which is nine times a first-shell count and was a labelling error, not a
wrong number. And the default `RATIONAL` switching function (NN=6, MM=12) decays slowly, partially
counting second-shell neighbours past the nominal R_0 — so 8.16 is **not** the hard-cutoff
first-shell number (~4.5) and asserting it against that literature value would be
[comparing a method difference](../../practices/cross-checks.md).

### Pins

| | data tier |
|---|---|
| PLUMED 2.9.2 / GROMACS 2026.3 | both in `quay.io/aarchsci/md@sha256:285377124854…` (`linux/arm64`) |
| water template | `spc216.gro` + `amber99sb-ildn`/tip3p — ship inside the GROMACS package |

**Repinned 2026-10-17** from `md@sha256:1ee941664add…`, whose env can no longer be re-solved:
conda-forge later capped its mdtraj 1.11.1 at `numpy <2.5` while the image ships numpy 2.5.2
([why that matters](../../practices/environment-durability.md)). PLUMED and GROMACS are the same
builds on both digests. The coordination number is read off a trajectory, which is a path, so it
moves run to run: 40.802 on the old digest, 40.950 and 40.801 on two runs of this one.

Nothing is staged: `gmx solvate` builds the 6.2 nm box from the packaged 216-water template, so the
recipe has no `stage-inputs.sh`. `PLUMED_KERNEL` must point at `libplumedKernel.so` for the
`-plumed` flag to work, which the task sets from the env's root (`$CONDA_PREFIX`, else
`/opt/conda`).

Every log the task writes is staged out — `mdrun.log`, `grompp.log`, `mdrun_em.log`, `solvate.log`,
`grompp_em.log` — because `command.log` only reaches S3 at stage-out, so an unstaged tool log dies
with the instance and a failed run leaves nothing to diagnose.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=plumed)" --wait
make ls RECIPE=plumed
```

Expect `smoke-check.txt` with `distance_cv 0.09572`, `angle_cv 1.82422` and `colvar_rows 501`.

### x86_64

Verified on `c8i.2xlarge` (2026-10-17) from the env's linux-64 twin (`envs-x86_64/md-f7ed8789d24e`,
228 of 245 versions identical, PLUMED 2.9.2 and GROMACS 2026.3 `mpi_openmpi`). Its BLAS is **MKL**
where arm64's is OpenBLAS (the lock header records why).

| | Graviton4 (`c8g.2xlarge`) | x86 (`c8i.2xlarge`) | kind |
|---|---|---|---|
| COLVAR rows | 501 | 501 | exact, identical |
| O-H distance / spread | 0.09572 nm / 0.0e+00 | 0.09572 nm / 0.0e+00 | constraint, identical |
| H-O-H angle / spread | 1.82422 rad / 7.0e-06 | 1.82422 rad / 1.0e-05 | constraint holds; spread is round-off |
| coordination total | 40.801 | 41.061 | path, reported |

</details>
