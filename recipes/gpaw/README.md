---
tool: gpaw
tool_version: 25.7.0
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.111.4
last_verified: 2026-09-30
---
# GPAW — a Pt(111) slab in plane-wave DFT, and the same energy from every box

Runs a 36-atom Pt(111) surface SCF (PW 400 eV, 4×4×1 k-points, PBE) and prices it across four Graviton generations and three core counts. For anyone running plane-wave DFT on ARM.

> **Nine runs, one number: −219.541414 eV in 27 SCF iterations, every time.** Four chips, three rank counts, three instance sizes. DFT is deterministic, so that is an exact identity — not a tolerance.

## Run it

```bash
make run RECIPE=gpaw   # ~7.4 min on c8g.4xlarge at 16 ranks, self-terminating
make ls  RECIPE=gpaw   # smoke-check.txt + gpaw.txt

mpiexec -n 16 python3 slab.py   # ASE builds Pt(111) 3×3×4; PAW datasets ship in the image
```

## Which box, and how many cores — both measured

| | instance | SCF wall | **$/SCF** |
|---|---|---|---|
| Graviton2, 16 ranks | `c6g.4xlarge` | 779.3 s | 0.1178 |
| Graviton3, 16 ranks | `c7g.4xlarge` | 479.8 s | **0.0773** |
| Graviton4, 16 ranks | `c8g.4xlarge` | 442.5 s | 0.0784 |
| **Graviton5, 16 ranks** | `c9g.4xlarge` | **334.6 s** | **0.0646** |
| Graviton4, 8 ranks | `c8g.2xlarge` | 789.4 s | 0.0699 |
| Graviton4, 4 ranks | `c8g.xlarge` | 1446.2 s | **0.0641** |

Graviton2→5 is **2.33× faster and 45% cheaper per SCF** — but **Graviton3 and Graviton4 are tied on cost** (1.4% apart, indistinguishable at n = 1), because Graviton4 is 8% faster for 10% more per hour. The only place in this catalog where "newest is always cheaper" fails; Graviton5 still clearly wins.

On cores, **16 ranks is 3.27× faster than 4 for 22% more money** — 82% parallel efficiency, better than DFT's reputation. 4 ranks is cheapest per SCF; 16 is the better buy if wall-clock matters. Each rank row ran on the box you would actually rent for it, which turns out to matter by 18% (below).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Pt(111) 3×3×4 | your own ASE structure | built in Python from ASE's lattice constants, so the cell is pinned by the image and nothing is staged. |
| `PW(400)`, `kpts=(4,4,1)` | your convergence settings | both change the answer — re-converge before comparing to anything. |

**Leave the workload** — a production-sized slab at production settings, so the timings transfer. **Scale it** by cell size or k-points, both of which cost real money, and re-measure.

<details>
<summary>As shipped: the exact identity, what measuring beat projecting, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| atoms | 36 | **36** |
| **MPI ranks used** | **== ranks launched** (`gpaw.mpi.world.size`) | **matches, 4/8/16** |
| SCF converged | `Converged after N iterations` present | **27 iterations** |
| total energy | finite, negative | **−219.541414 eV** |
| Fermi level | −10 to 10 eV | **2.050224 eV** |
| **energy across rank counts** | **spread < 1e-4 eV** | **0.000000** |

**The rank-count assertion is load-bearing.** conda-forge ships nompi builds at higher build numbers
than the openmpi ones, so an unpinned solve can hand back a serial GPAW that under `mpiexec -n 16`
runs sixteen independent single-rank SCFs — each converging to the right energy and printing a
plausible wall time. Reading `gpaw.mpi.world.size` back is what proves the parallelism
([the practice](../../practices/mpi-rank-count.md)).

**And the convergence sentinel matters as much.** GPAW writes `Converged after N iterations` only on
a clean SCF exit; a run that stops on the iteration limit still has a plausible-looking energy and no
such line. Asserting the line's presence is what separates a converged result from an abandoned one.

**Why an exact energy identity is available here and not in MD.** DFT total energy is deterministic
— the SCF converges to a fixed point — so dividing the work differently must not change it. All nine
runs agreed to all six printed decimals. The [MD recipes](../gromacs/README.md) cannot assert this
because trajectories are chaotic; DFT can, and it costs nothing.

### Measuring the knee beat projecting it, by 18%

The three knee rows were run on `xlarge`/`2xlarge`/`4xlarge` — the box you would rent for that rank
count. Running all three on one 16-core box instead would have been cheaper and wrong: **4 ranks took
1227.0 s on the 16-core box but 1446.2 s on a 4-core box**, 17.9% slower, because a smaller instance
gets a smaller share of memory bandwidth and plane-wave DFT is bandwidth-bound. Projecting the
4-rank cost from the big box gives $0.0544 against the measured $0.0641 — **an 18% understatement of
the cheapest option**, which is exactly the number a reader would be deciding on.

### Pins

| | data tier |
|---|---|
| GPAW | `quay.io/aarchsci/dft@sha256:0740fab9…` (25.7.0, ASE 3.29.0, `linux/arm64`) |
| structure | built by `ase.build.fcc111` — ASE's own Pt lattice constant, pinned by the image |
| PAW datasets | ship inside the image |

Nothing is staged, which is why this recipe has no `stage-inputs.sh`: the cell is six lines of
Python and the datasets are in the container. The trade is that the structure is pinned by the
*image* rather than by a hash — change the ASE version and the lattice constant could move, which
would move the energy. That is the tier, recorded.

### Run + verify

```sh
make run RECIPE=gpaw
make ls  RECIPE=gpaw
```

Expect `smoke-check.txt` with `atoms 36`, `ranks_used 16`, `scf_iterations 27` and
`energy_eV -219.541414`.

</details>
