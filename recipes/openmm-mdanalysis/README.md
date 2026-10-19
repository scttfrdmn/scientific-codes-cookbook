---
tool: openmm
tool_version: 8.6.1.dev-b399af4
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d
env_x86_64: comp-chem-1243f25ebfec
spawn_version: 0.130.1
last_verified: 2026-10-17
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# OpenMM — 20 ps of NVE on 1,728 atoms, read back by MDAnalysis

Conserves energy to 2.5e-07 over 20,000 steps, then has MDAnalysis recover exactly what OpenMM wrote. For anyone running OpenMM or reading its output.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=openmm-mdanalysis)" --wait   # 7 s of MD
make ls RECIPE=openmm-mdanalysis   # top.pdb + traj.dcd + omm.json + smoke-check.txt
```

```python
sim = app.Simulation(top, system, mm.VerletIntegrator(1*unit.femtosecond), mm.Platform.getPlatformByName("CPU"))
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

**Leave the size** for the check (20,000 steps already accumulate drift); the measured runs below carry it to production scale.

## Shape, size, cost

`c8g.2xlarge`: **20 ps in 7 s**. **Which box — [measured](../../measurements/openmm-mdanalysis-real/README.md)** (Amber20 suite: DHFR 23,558 atoms × 1 ns NVE, STMV 1,067,095 atoms; 32 threads, `8xlarge`):

| chip | DHFR ns/day | STMV ns/day | **$/ns DHFR** |
|---|---|---|---|
| **Graviton4** `c8g` | **66.5** | **1.70** | **0.46** |
| Intel `c8i` | 51.3 | 0.97 | 0.70 |
| AMD `c8a` | 55.3 | 1.39 | 0.75 |

Graviton4 is 34–38% cheaper per ns; OpenMM matches `sander` term by term on all three, and caught a `sander` overflow bug.

<details>
<summary>As shipped: an energy identity, a cross-layer decode, and the units bug the decode caught</summary>

### The checks

| observable | assertion | observed (Graviton4) | x86_64 (`c8i`) |
|---|---|---|---|
| **NVE conserved** | **drift < 1e-5 relative** | **2.50e-07** over 20 ps (1.3e-11/step) | **5.32e-09** (2.7e-13/step) |
| platform | the context ran on `CPU` | **CPU** (loaded: Reference, CPU) | identical |
| atoms | 1,728 = 12³, OpenMM == MDAnalysis | **1,728** | identical |
| frames | 100 (every 200 of 20,000 steps) | **100** | identical |
| box | MDAnalysis == OpenMM's box | **45.840 Å** | identical |
| **lattice spacing** | **nearest neighbour == 3.820 Å** | **3.820** | identical |

**Energy conservation is the right identity for an integrator.** With no thermostat, total energy
must be constant, and the error *accumulates* — so the test sharpens with system size and run
length. The previous version of this recipe ran 27 atoms for 200 steps, where there is barely time
to drift; 1,728 atoms over 20,000 steps conserves to 2.50e-07 relative, or 1.3e-11 per step.

**The threshold is 1e-5, not the observed value, and that is deliberate.** Runs of the identical
spec gave 2.21e-07, 2.96e-07 and 2.50e-07 — OpenMM's CPU platform reduces forces in a thread-dependent order,
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
| OpenMM 8.6.1.dev / MDAnalysis 2.10.0 | both in `quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cf…` (`linux/arm64`) |
| system | built in Python: 12³ argon, 0.382 nm spacing, σ=0.34 nm, ε=0.996 kJ/mol |

Nothing is staged, so there is no `stage-inputs.sh` — the trade is that the system is pinned by the
*image* rather than by a hash. Versions are read from inside the run rather than recorded by hand.

The previous pin, `comp-chem@sha256:a06f130ca3c8…` (lock-hash `c8cd4a3c4c30`), can no longer be
re-solved: conda-forge later capped its mdtraj 1.11.1 at `numpy <2.5`, and it shipped numpy 2.5.2
(found 2026-10-17). It still runs, and its arm64 lock still installs without a solve
([why](../../practices/environment-durability.md)); this recipe moved to the 2026.10.11 env.

### x86_64

The same spec on the env's linux-64 twin (`envs-x86_64/comp-chem-1243f25ebfec`, 228 packages; three
versions differ from arm64, none of them OpenMM's or MDAnalysis's, and it adds `cuda-nvrtc` and `libcufft`). conda-forge's linux-64 OpenMM
is a CUDA build, so the platform check is the one that matters here: on a GPU-less `c8i.2xlarge`
only `Reference` and `CPU` load, and the context ran on `CPU`. Every structural value is identical
across chips. The drift is not, and should not be: it is a **path**, accumulated round-off over
20,000 steps, already different run to run on one chip. Both sit well inside 1e-5. 20 ps took 9 s.

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=openmm-mdanalysis)" --wait
make ls RECIPE=openmm-mdanalysis
```

Expect `smoke-check.txt` with `nve_conserved` under 1e-5 and `lattice_spacing_decode 3.820`.

</details>
