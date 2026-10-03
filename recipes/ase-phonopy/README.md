---
tool: phonopy
tool_version: 4.4.0
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.115.0
last_verified: 2026-10-03
---
# ASE → spglib → phonopy — phonons of bulk silicon

Three tools in a chain — ASE builds a silicon crystal, spglib finds its symmetry, phonopy computes Γ-point phonons — the phonon-calculation pipeline for any crystal.

> **What this covers.** Forces come from a generic Lennard-Jones field, so the phonon *frequencies* are not silicon's real spectrum. No DFT forces, dispersion, or thermodynamics.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=ase-phonopy)" --wait
```
```python
import numpy as np
from ase.build import bulk
from phonopy import Phonopy
from phonopy.structure.atoms import PhonopyAtoms
si = bulk("Si", "diamond", a=5.43)                         # ASE builds bulk Si (Fd-3m)
unit = PhonopyAtoms(symbols=si.get_chemical_symbols(), cell=si.cell[:],
                    scaled_positions=si.get_scaled_positions())
ph = Phonopy(unit, supercell_matrix=np.eye(3) * 2)         # 2×2×2 supercell; spglib finds the symmetry
ph.generate_displacements(distance=0.03)                   # → ONE displacement: the chain link made visible
# fill ph.forces from a LennardJones calculator on ph.supercells_with_displacements (DFT for real work)
ph.produce_force_constants()
freqs = ph.get_frequencies([0, 0, 0])                      # 3 acoustic → 0, 3 optical degenerate
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| bulk Si diamond cell (built in code) | your own crystal (ASE `Atoms` or a CIF) | the space-group and sum-rule identities hold for any crystal; Si is the hand-checkable case. |
| Lennard-Jones forces | a real calculator (DFT via [gpaw](../gpaw/README.md)) | **load-bearing to state:** LJ forces make the *frequencies* wrong for Si, but the acoustic sum rule holds for **any** translationally-invariant potential, so it still validates the chain. Swap in real forces for real phonons. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the sum rule is a construction where physics forces an exact answer regardless of cell size, so a bigger supercell is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Measured phase split for the verifying run: Docker install **40 s**, `dft` image pull **78 s**, the whole ASE → spglib → phonopy chain **5 s**. Provisioning is 96% of the 123 s wrapper window ([why](../../practices/what-this-does-not-cover.md)), so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

**Sizing:** phonopy's own work (displacements, force constants) is light on any box; the cost of real phonons is the DFT force evaluation per displacement — size that on the DFT code ([gpaw](../gpaw/README.md)), not phonopy.

<details>
<summary>As shipped: the physical identities, pins, smoke-check table, run + verify</summary>

### The checks — two physical identities, no fitted bands

- **Space group is exact-or-wrong.** Silicon is `Fd-3m` (#227); no tolerance to argue.
- **Acoustic modes → 0 at Γ is a conservation-class identity.** Translational invariance (the acoustic sum rule) forces the three acoustic branches to vanish at the zone center — physics forcing an exact answer, the same class as [ambertools](../ambertools/README.md)' NVE. A wrong force-constant chain gives THz-scale nonzero acoustic modes. (Bonus: the three optical modes at Γ are triply degenerate — T₂g — to machine precision.)

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` (tag `2026.09.04`, cosign-signed, `linux/arm64`) — the run records its own chain versions: **ASE 3.29.0, spglib 2.7.0, phonopy 4.4.0** |
| input | bulk-Si cell, built in code — nothing staged |

Same `dft` image as [gpaw](../gpaw/README.md) and [nwchem](../nwchem/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| ASE unit cell | exactly 2 atoms (Si primitive) | 2 | wrong build |
| **space group** | `Fd-3m (227)` (spglib) | Fd-3m (227) | broken symmetry detection |
| symmetry-reduced displacements | exactly 1 | 1 | spglib didn't cut the set |
| **acoustic modes at Γ** | max < 1e-2 THz (→ 0 by the sum rule) | 7.0e-7 | broken force-constant chain |
| optical degeneracy | 3 optical modes' spread < 1e-3 THz (T₂g) | 5.3e-15 | broken symmetry |

The acoustic band (1e-2 THz) is method-justified — a residual from the 0.03 Å finite displacement and floating point, not a value picked to pass; analytically the modes are exactly zero.

**Why a band and not an exact identity, when the analytic answer is exactly 0.** The residual is pure floating-point noise, and noise is not portable: the same pinned image gives `7.02e-07 / 3.55e-15` on an Apple-Silicon scratch run and `6.99e-07 / 5.33e-15` on Graviton4, because the eigensolver's reduction order differs. Asserting the observed digits would be [exact one run and different the next](../flye/README.md) — the subtlest way a check goes bad. The band is nine orders of magnitude above the observed value and nine below a broken chain's, so it cannot be flaky in either direction.

### Run + verify

```sh
make run RECIPE=ase-phonopy
make ls RECIPE=ase-phonopy
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect one object (`smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
