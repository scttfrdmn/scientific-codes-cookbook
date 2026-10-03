---
tool: plumed
tool_version: 2.9.2
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.111.4
last_verified: 2026-10-02
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

```
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

**Leave the system** — 23k atoms with PLUMED in the loop is a realistic coupling cost. **Scale it**
by CV expense: a `COORDINATION` over every pair is O(N²) per step and will dominate GROMACS.

## Shape, size, cost

`c8g.2xlarge`: minimisation + **100 ps of coupled NVT in 154 s**, ~$0.02. GROMACS's own
generation scaling is measured in [its recipe](../gromacs/README.md); PLUMED adds the CV cost on top.

<details>
<summary>As shipped: CVs checked against force-field constants, and one observation deliberately not asserted</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| coupling ran | GROMACS finished **and** PLUMED wrote CVs | **501 rows, mdrun finished** |
| COLVAR rows | exactly 501 | **501** |
| **O-H distance** | **0.09572 nm** (TIP3P, via PLUMED `DISTANCE`) | **0.09572** |
| **H-O-H angle** | **1.82422 rad** = 104.52° (via `ANGLE`) | **1.82422** |
| distance invariant | spread < 1e-4 nm over 501 frames | **2.0e-06** |
| angle invariant | spread < 1e-3 rad | **1.5e-05** |

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
| `COORDINATION` total | **40.802** (sum over 5 central O × all O pairs) |
| per central atom | **8.160** within R_0 = 0.35 nm |
| per-atom range | 7.135 … 9.013 |

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
| PLUMED 2.9.2 / GROMACS | both in `quay.io/aarchsci/md@sha256:1ee941664add…` (`linux/arm64`) |
| water template | `spc216.gro` + `amber99sb-ildn`/tip3p — ship inside the GROMACS package |

Nothing is staged: `gmx solvate` builds the 6.2 nm box from the packaged 216-water template, so the
recipe has no `stage-inputs.sh`. `PLUMED_KERNEL` must point at `libplumedKernel.so` for the
`-plumed` flag to work, which the task sets.

Every log the task writes is staged out — `mdrun.log`, `grompp.log`, `mdrun_em.log`, `solvate.log`,
`grompp_em.log` — because `command.log` only reaches S3 at stage-out, so an unstaged tool log dies
with the instance and a failed run leaves nothing to diagnose.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=plumed)" --wait
make ls RECIPE=plumed
```

Expect `smoke-check.txt` with `distance_cv 0.09572`, `angle_cv 1.82422` and `colvar_rows 501`.

</details>
