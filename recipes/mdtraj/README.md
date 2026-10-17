---
tool: mdtraj
tool_version: 1.11.2
env: md
image: quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f
gap_x86_64: the committed linux-64 twin of this `md` image, envs-x86_64/md-f7ed8789d24e.linux-64.lock, resolves gromacs, lammps and plumed to their nompi builds (gromacs-2026.3-nompi_h26635d9_100 ships `bin/gmx` and no `gmx_mpi`; lammps-2025.07.22-cpu_py314_h4769034_nompi_6 ships `lmp`/`lmp_serial` and no `lmp_mpi`), though md.yaml pins `gromacs=*=mpi_openmpi*` and `lammps=*=cpu_*mpi_openmpi*`; conda-forge has linux-64 mpi_openmpi builds of all three, so this is a twin to re-solve (found 2026-10-17), not a missing x86 build
spawn_version: 0.130.1
last_verified: 2026-10-17
---
# MDTraj — read a real 100 ps trajectory, checked against MDAnalysis byte for byte

Runs 100 ps of 23,262-atom water in GROMACS, then has MDTraj and MDAnalysis decode the same XTC and agree to 2.4e-07 nm. For anyone analysing trajectories.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=mdtraj)" --wait   # ~4 min on c8g.2xlarge
make ls RECIPE=mdtraj   # out.xtc + smoke-check.txt + the GROMACS logs
```

```python
import mdtraj as mdt, MDAnalysis as mda
t = mdt.load("out.xtc", top="big.gro")          # GROMACS XTC + .gro topology
u = mda.Universe("big.gro", "out.xtc")          # the SAME bytes, a second parser
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 7,754-water box | your own system | nothing is staged — spc216 and tip3p ship in the GROMACS package. |
| 100 ps / 501 frames | longer | the XTC is 41 MB at this length; analysis is linear in frames. |
| `-ntomp 8` | your cores | changes the trajectory, so **no coordinate is asserted** — see below. |
| MDTraj + MDAnalysis | either alone | the point of running both is that neither then checks the other. |

**Leave the system** — a real solvated box at 23k atoms, so the parse and the analysis are both at
realistic size. **Scale it** by frames or atoms; both cost linearly.

## Shape, size, cost

`c8g.2xlarge`: minimisation + **100 ps of NVT in 151 s**, ~$0.02, producing a 41 MB XTC.
No generation table — the interesting cost here is GROMACS's, which
[its own recipe](../gromacs/README.md) measures across four generations.

<details>
<summary>As shipped: a parser identity that survives chaotic dynamics, a constraint not a band, and the collapsed run that taught the box check</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| atoms | exactly 23,262, both libraries | **23,262** |
| frames | exactly 501, both libraries | **501** |
| **coordinates** | **max\|MDTraj − MDAnalysis\| < 1e-5 nm** | **2.38e-07** over 34,962,786 values |
| box volume | max\|diff\| < 1e-3 nm³ | **2.76e-05** |
| **O-H distance** | **0.09572 ± 0.0005 nm** | **0.09572** |
| Rg fills the box | within 0.25 nm of L/2 | **3.1007** vs **3.1000** |

**The coordinate identity is the one that matters, and it works *because* MD is chaotic.** Both
libraries read the *same file*, so their agreement is a property of the parsers, not of the dynamics:
35 million values agreeing to 2.4e-07 nm is float32 XTC precision, so the decoders are equivalent.

That the dynamics are *not* reproducible is measured, not assumed. Two runs with the same
`gen-seed 42` and the same `-ntomp 8` gave a different trajectory — first water's final frame
`1.2240 0.2740 0.4580` against `4.9140 1.4590 5.8840`, Rg 3.1003 against 3.1006 — because GROMACS's
dynamic load balancing and PME reduction order are not bit-stable. So no coordinate can be asserted
here, while `coords_two_libraries` held at exactly 2.38e-07 across both runs. The invariants that
survive are properties of the system and the force field, not of the run: atom count, frame count,
the tip3p constraint, and Rg ≈ L/2.

**Why coordinates and not radius of gyration.** The first version compared mass-weighted Rg and
failed at 4.9e-04 nm. Both libraries *guess* masses from atom names in a `.gro`, and their tables
differ slightly — confirmed here, MDTraj 3.1007 against MDAnalysis 3.1003 on identical coordinates.
That is a [method difference, not a parser bug](../../practices/cross-checks.md); Rg is reported, not
asserted. Coordinates admit no model difference at all, which is what makes them the right
cross-check.

**The O-H distance is a constraint, not a band.** `constraints = h-bonds` holds every bond at tip3p's
0.09572 nm exactly, and XTC stores coordinates to 0.001 nm — so the window comes from the force field
and the file format, and a median outside it means the constraint was never applied rather than that
the water moved. The observed median lands on 0.09572 to five decimals, with min 0.09416 and max
0.09729 from the rounding.

### The collapsed run, and the check that now catches it

The first attempt ran `gmx solvate` straight into `mdrun` with no minimisation, plain cut-off
electrostatics and no thermostat. It completed, wrote 501 frames and a plausible 27 MB XTC — and was
wrong: **Rg 0.796 nm in a 6.2 nm box**, with most O-H pairs at distance 0. Solvation packs
overlapping waters, and dynamics started from those degenerate.

Nothing in the original check set caught it. The O-H assertion failed, but as a symptom four steps
downstream, which sent me looking at indexing and periodic wrapping instead of at the trajectory.
`rg_fills_box` is the fix: a uniformly filled periodic box must have Rg ≈ L/2, so the number to
compare against is **3.1000**, derived from the box, not from a previous run. It read 0.796 then and
3.1007 now.

The protocol is now minimise (`steep`, 2000 steps, PME) → NVT (`v-rescale`, 300 K, `gen-seed 42`),
and `mdrun.log`, `grompp.log` and `mdrun_em.log` are **staged out**, because the first two failures
were diagnosed blind: the logs existed inside the task and died with the instance. Same lesson
[flye](../flye/README.md) carries.

### Pins

| | data tier |
|---|---|
| MDTraj 1.11.2 / MDAnalysis 2.10.0 / GROMACS 2026.3 | all in `quay.io/aarchsci/md@sha256:285377124854…` (`linux/arm64`) — versions read from the run |
| water template | `spc216.gro` + `amber99sb-ildn`/tip3p — ship inside the GROMACS package |

**Repinned 2026-10-17** from `md@sha256:1ee941664add…` (MDTraj 1.11.1), whose env can no longer be
re-solved: conda-forge later capped that mdtraj at `numpy <2.5` while the image ships numpy 2.5.2
([why that matters](../../practices/environment-durability.md)). Every assertion held on the new
digest at the same observed values; Rg moved in the fourth decimal, as a trajectory does run to run.

Nothing is staged: `gmx solvate` builds the 6.2 nm box from the packaged 216-water template, so the
recipe has no `stage-inputs.sh` and no external data to pin. The trade is that the structure is
pinned by the *image* rather than by a hash.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=mdtraj)" --wait
make ls RECIPE=mdtraj
```

Expect `smoke-check.txt` with `coords_two_libraries` under 1e-5, `oh_constraint 0.09572` and
`rg_fills_box` near 3.1000.

### x86_64

Not verified yet. The committed linux-64 twin of this image resolved GROMACS to its nompi build,
which has no `gmx_mpi`, so the task cannot produce its trajectory; it needs re-solving with
`md.yaml`'s `mpi_openmpi` pin. When it runs, the parser identity and the constraint must hold
exactly on x86 too, while the trajectory itself is a path and will differ — more so because that
twin's BLAS is MKL where arm64's is OpenBLAS.

</details>
