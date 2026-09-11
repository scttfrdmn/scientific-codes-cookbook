---
tool: openmm
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.104.0
---
# OpenMM → MDAnalysis — write an NVE trajectory, read it back and check it

OpenMM runs a short NVE simulation and writes a topology + trajectory; MDAnalysis reads them back — the simulate-then-analyze handoff.

> **What this covers.** A 27-atom argon NVE run (200 steps) analyzed by MDAnalysis — proof OpenMM's integrator and MDAnalysis's DCD/PDB readers work, and hand off correctly, on Graviton4. Not a benchmark; no biomolecular force field, thermostat/barostat, or long trajectory.

## Run it

```python
# OpenMM ran a 27-atom argon NVE (200 steps) and wrote top.pdb + traj.dcd; MDAnalysis reads them back:
import MDAnalysis as mda
u = mda.Universe("top.pdb", "traj.dcd")
u.atoms.n_atoms, len(u.trajectory), u.dimensions[:3]   # 27, 10, 11.460 Å — exactly what OpenMM wrote
```

One task: OpenMM writes the trajectory and MDAnalysis reads it in the same container. The identity is about the *format handoff* (OpenMM's writer ↔ MDAnalysis's reader), not the storage path, so routing through S3 would add a boot for no scientific gain. The system is built in code, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 27-atom argon lattice (built in code) | your own system + force field | argon on a Lennard-Jones potential is a hand-checkable NVE test; the readers don't care about the force field. |
| NVE, no thermostat | a thermostat / barostat | NVE is what makes energy conservation an *exact* check — add a thermostat and you check temperature control instead. |

Deterministic on fixed input — **nothing is determinism scaffolding**. **Leave the fixture:** the decode identities are exact-or-wrong at any trajectory length, and a short run keeps the check fast. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Simulation + analysis take ~1 s. Recorded command window **70s** — boot, Docker install, and the ~0.62 GB `comp-chem` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the two identities, pins, smoke-check table, run + verify</summary>

### The checks — energy conservation + a cross-layer decode

- **OpenMM: NVE energy conservation.** With no thermostat, kinetic + potential energy must be conserved — a physical law, the same class as [ambertools](../ambertools/README.md)' check on a different engine. Observed relative drift **3.9e-7** over 200 steps (a switching function on the LJ cutoff and a 1 fs timestep make it that clean).
- **Cross-layer decode: MDAnalysis recovers what OpenMM wrote.** Exact atom count, frame count and box, plus the lattice spacing read back from the coordinates. A writer/reader mismatch (endianness, unit, frame stride) fails these even if both tools "ran".

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` (tag `2026.09.04`, OpenMM + MDAnalysis + …, cosign-signed, `linux/arm64`) |
| input | 27-atom argon lattice, built in code — nothing staged |

Same `comp-chem` image as [pyscf](../pyscf/README.md), [rdkit](../rdkit/README.md), [vina](../vina/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| **NVE conserved** | relative drift < 1e-4 over 200 steps | 3.91e-7 | broken integrator |
| **MDA atoms == OpenMM** | exactly 27 | 27 | wrong decode |
| **MDA frames** | exactly 10 (DCD every 20 of 200) | 10 | wrong frame stride |
| **MDA box == OpenMM** | 11.460 Å | 11.460 | box not recovered |
| **lattice spacing decode** | nearest-neighbour 3.820 Å (from coords) | 3.820 | coordinates mangled |

The NVE drift is a physics band (must conserve; 1e-4 is cleared by ~250×), robust to cross-host floating point. The other four are exact structural identities.

### Run + verify

```sh
make run RECIPE=openmm-mdanalysis
make ls RECIPE=openmm-mdanalysis
```

a completed run does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect four objects (`top.pdb`, `traj.dcd`, `omm.json`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
