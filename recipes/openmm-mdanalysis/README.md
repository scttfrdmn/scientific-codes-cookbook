---
tool: openmm
tool_version: 8.6.0.dev-c6173db
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.111.4
last_verified: 2026-10-02
---
# OpenMM — 20 ps of NVE on 1,728 atoms, read back by MDAnalysis

Conserves energy to 3e-07 over 20,000 steps, then has MDAnalysis recover exactly what OpenMM wrote. For anyone running OpenMM or reading its output.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=openmm-mdanalysis)" --wait   # 7 s of MD
make ls RECIPE=openmm-mdanalysis   # top.pdb + traj.dcd + omm.json + smoke-check.txt
```

```python
sim = app.Simulation(top, system, mm.VerletIntegrator(1*unit.femtosecond),
                     mm.Platform.getPlatformByName("CPU"))
sim.context.setPositions(unit.Quantity(pos, unit.nanometer))   # units are load-bearing
sim.step(20000)                                                # no thermostat: NVE
u = mda.Universe("top.pdb", "traj.dcd")                        # the other layer reads it back
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 1,728-atom argon | your own system | built in Python from OpenMM primitives, so nothing is staged. |
| `VerletIntegrator` (NVE) | Langevin / a thermostat | **the energy identity goes away** — a thermostat exchanges energy by design. |
| 100 K | your temperature | LJ argon melts near 84 K, so 100 K is a fluid; below that you are simulating a solid. |
| `CPU` platform | `CUDA` / `OpenCL` | `OPENMM_CPU_THREADS` controls the CPU platform; drift is thread-order dependent (below). |

**Leave the size.** 7 s of compute already makes drift accumulate over 20,000 steps, which is what
the identity tests; a bigger box would cost more without sharpening it. **Scale it** if you need
throughput numbers rather than a correctness check.

## Shape, size, cost

`c8g.2xlarge` (8 threads): **20 ps in 7 s**, well under a cent. No generation table — at 7 s a
four-chip ladder would measure boot ([same call as mash](../mash/README.md)).

<details>
<summary>As shipped: an energy identity, a cross-layer decode, and the units bug the decode caught</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| **NVE conserved** | **drift < 1e-5 relative** | **2.96e-07** over 20 ps (1.5e-11/step) |
| atoms | 1,728 = 12³, OpenMM == MDAnalysis | **1,728** |
| frames | 100 (every 200 of 20,000 steps) | **100** |
| box | MDAnalysis == OpenMM's box | **45.840 Å** |
| **lattice spacing** | **nearest neighbour == 3.820 Å** | **3.820** |

**Energy conservation is the right identity for an integrator.** With no thermostat, total energy
must be constant, and the error *accumulates* — so the test sharpens with system size and run
length. The previous version of this recipe ran 27 atoms for 200 steps, where there is barely time
to drift; 1,728 atoms over 20,000 steps conserves to 2.96e-07 relative, or 1.5e-11 per step.

**The threshold is 1e-5, not the observed value, and that is deliberate.** Two runs of the identical
spec gave 2.21e-07 and 2.96e-07 — OpenMM's CPU platform reduces forces in a thread-dependent order,
so accumulated round-off is not reproducible. Pinning 2.21e-07 would have been exact once and flaky
after ([the rule](../../practices/cross-checks.md)). 1e-5 catches a broken integrator or a thermostat
left enabled; the per-step figure is what transfers to a different run length, since the total
depends on how long you ran.

### The decode identity caught a real bug

MDAnalysis reads OpenMM's `top.pdb` and `traj.dcd` back and must recover the atom count, the frame
count, the box, and — from the coordinates alone — the lattice spacing OpenMM built.

That last one earned its place. A bare list of `mm.Vec3` is **nanometres** to `setPositions` but
**Ångström** to `PDBFile.writeFile`, so writing the raw list produced a PDB ten times too small:
`lattice_spacing_decode` read **0.382 Å** where 3.820 was required, while every other check passed.
The fix is to carry units explicitly — `unit.Quantity(pos, unit.nanometer)` for both calls — and the
lesson is that a decode identity on a *derived geometric quantity* finds unit errors that count-based
checks cannot.

### Pins

| | data tier |
|---|---|
| OpenMM 8.6.0.dev / MDAnalysis 2.10.0 | both in `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8…` (`linux/arm64`) |
| system | built in Python: 12³ argon, 0.382 nm spacing, σ=0.34 nm, ε=0.996 kJ/mol |

Nothing is staged, so there is no `stage-inputs.sh` — the trade is that the system is pinned by the
*image* rather than by a hash. Versions are read from inside the run rather than recorded by hand.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=openmm-mdanalysis)" --wait
make ls RECIPE=openmm-mdanalysis
```

Expect `smoke-check.txt` with `nve_conserved` under 1e-5 and `lattice_spacing_decode 3.820`.

</details>
