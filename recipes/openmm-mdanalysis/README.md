# OpenMM → MDAnalysis — an NVE trajectory written by one tool, read back and checked by another

One task, two tools, one workflow. OpenMM runs a short NVE molecular dynamics simulation
and writes a topology + trajectory; MDAnalysis reads them back. The smoke check confirms
OpenMM conserved energy **and** that MDAnalysis recovers exactly what OpenMM wrote — a
cross-layer identity, not two isolated checks.

> **What this recipe does and does not cover.** It runs a 27-atom argon NVE simulation
> (200 steps) and analyzes the trajectory — enough to prove OpenMM's integrator and
> MDAnalysis's DCD/PDB readers work correctly, and hand off correctly, on Graviton4. Not a
> benchmark; no biomolecular force field, thermostat/barostat, or long trajectory.

## Why the pairing, and why one task

OpenMM is a third MD engine in the catalog (after GROMACS and LAMMPS) and MDAnalysis is
the analysis layer; run together, **an OpenMM trajectory read back by MDAnalysis is a real
workflow** rather than two tool checks in isolation. Both are in the `comp-chem` image, so
this is one task: OpenMM writes `top.pdb` + `traj.dcd`, MDAnalysis reads them in the same
container. The identity is about the **format handoff** (OpenMM's writer ↔ MDAnalysis's
reader), not the storage path, so routing the trajectory through S3 between two tasks would
add a boot for no scientific gain — the catalog already has plenty of S3-between examples.
Zero staging: the system is built in code.

## Two kinds of identity

- **OpenMM: NVE energy conservation.** With no thermostat, kinetic + potential energy must
  be conserved — a physical law, the same class as `recipes/ambertools`' check, and a
  **second independent instance** of it in the catalog on a different engine. Observed
  relative drift **3.9e-7** over 200 steps (a switching function on the Lennard-Jones
  cutoff and a 1 fs timestep are what make it that clean).
- **Cross-layer decode: MDAnalysis recovers what OpenMM wrote.** Exact atom count, frame
  count and box dimensions, plus the lattice spacing read back from the coordinates — the
  same decode-statistic move as `recipes/earth-observation`'s GDAL checksum and
  `recipes/pointcloud`'s PDAL Z-mean, applied to a trajectory format. A writer/reader
  mismatch (wrong endianness, unit, or frame stride) fails these even if both tools "ran".

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` |
| | tag `2026.09.04`, OpenMM + MDAnalysis (+ pyscf, rdkit, …), cosign-signed, `linux/arm64` |
| input | 27-atom argon lattice, **built in code** — nothing staged |

**Data tier: none / in-task.** Same `comp-chem` image as `recipes/pyscf`, `recipes/rdkit`,
`recipes/vina`; this recipe uses OpenMM and MDAnalysis.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| **NVE conserved** | relative energy drift < 1e-4 over 200 steps | 3.91e-7 |
| **MDA atoms == OpenMM** | exactly 27 (what OpenMM built) | 27 |
| **MDA frames** | exactly 10 (what OpenMM wrote, DCD every 20 of 200) | 10 |
| **MDA box == OpenMM** | 11.460 Å (the periodic box OpenMM set) | 11.460 |
| **lattice spacing decode** | nearest-neighbour 3.820 Å (== lattice, from the coords) | 3.820 |

The NVE drift is a physics band (must conserve; 1e-4 is generous, cleared by ~250×) —
robust to cross-host floating point, not a value picked to pass. The other four are exact
structural/geometric identities that only hold if OpenMM wrote and MDAnalysis parsed the
formats correctly.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The simulation +
analysis take **~1 second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.62 GB** `comp-chem` image are the whole task. The recorded run's command window was
**70s** (01:30:16 → 01:31:26 UTC), NVE drift 3.9e-7 and MDAnalysis recovering all of
OpenMM's counts. TTL was **retightened from that first real run**: 10m → **5m**,
`cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius, not caution; the recorded
run used the original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — the system is built in code.

```sh
spawn task run --spec recipes/openmm-mdanalysis/01-md-analyze.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/openmm-mdanalysis/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect four
objects — `top.pdb`, `traj.dcd`, `omm.json`, `smoke-check.txt` (the topology and trajectory
are the artifacts).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
re-run.
