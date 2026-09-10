---
tool: phonopy
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.104.0
---
# ASE → spglib → phonopy — phonons of bulk silicon

Three tools in a chain: ASE builds a silicon crystal, spglib finds its symmetry, phonopy uses that symmetry to reduce the displacement set and compute Γ-point phonons. The final identity — acoustic modes → 0 at Γ — validates the *whole* chain at once.

> **What this covers.** Build one crystal, find its space group, compute Γ-point phonons — proof ASE, spglib and phonopy work and hand off correctly on Graviton4. Forces come from a generic Lennard-Jones field, so the phonon *frequencies* are not silicon's real spectrum; the asserted identities don't depend on that. No DFT forces, dispersion, or thermodynamics.

## Run it

```python
from ase.build import bulk
from phonopy import Phonopy
# ASE builds bulk Si (Fd-3m) → spglib reduces the 128-atom supercell's displacements
# to ONE symmetry-unique displacement → phonopy builds force constants → frequencies at Γ
freqs = phonopy_obj.get_frequencies([0, 0, 0])   # 3 acoustic → 0, 3 optical degenerate
```

One task; the chain runs in one container. spglib cutting the displacement set to a single unique displacement is the chain link made visible — if it returned the wrong space group, the force constants come out wrong and the acoustic modes don't vanish.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| bulk Si diamond cell (built in code) | your own crystal (ASE `Atoms` or a CIF) | the space-group and sum-rule identities hold for any crystal; Si is the hand-checkable case. |
| Lennard-Jones forces | a real calculator (DFT via [gpaw](../gpaw/README.md)) | **load-bearing to state:** LJ forces make the *frequencies* wrong for Si, but the acoustic sum rule holds for **any** translationally-invariant potential, so it still validates the chain. Swap in real forces for real phonons. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the sum rule is a construction where physics forces an exact answer regardless of cell size, so a bigger supercell is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Build + symmetry + phonons is sub-second. Recorded command window **94s** — boot, Docker install, and the ~0.87 GB `dft` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the physical identities, pins, smoke-check table, run + verify</summary>

### The checks — two physical identities, no fitted bands

- **Space group is exact-or-wrong.** Silicon is `Fd-3m` (#227); no tolerance to argue.
- **Acoustic modes → 0 at Γ is a conservation-class identity.** Translational invariance (the acoustic sum rule) forces the three acoustic branches to vanish at the zone center — physics forcing an exact answer, the same class as [ambertools](../ambertools/README.md)' NVE. A wrong force-constant chain gives THz-scale nonzero acoustic modes. (Bonus: the three optical modes at Γ are triply degenerate — T₂g — to machine precision.)

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` (tag `2026.09.04`, ASE + spglib + phonopy + pymatgen, cosign-signed, `linux/arm64`) |
| input | bulk-Si cell, built in code — nothing staged |

Same `dft` image as [gpaw](../gpaw/README.md) and [nwchem](../nwchem/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| ASE unit cell | exactly 2 atoms (Si primitive) | 2 | wrong build |
| **space group** | `Fd-3m (227)` (spglib) | Fd-3m (227) | broken symmetry detection |
| symmetry-reduced displacements | exactly 1 | 1 | spglib didn't cut the set |
| **acoustic modes at Γ** | max < 1e-2 THz (→ 0 by the sum rule) | 7.0e-7 | broken force-constant chain |
| optical degeneracy | 3 optical modes' spread < 1e-3 THz (T₂g) | 3.6e-15 | broken symmetry |

The acoustic band (1e-2 THz) is method-justified — a residual from the 0.03 Å finite displacement and floating point, not a value picked to pass; analytically the modes are exactly zero.

### Run + verify

```sh
spawn task run --spec recipes/ase-phonopy/01-phonons.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/ase-phonopy/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect one object (`smoke-check.txt`). Re-run: bump the `-r1` suffix. A transient `Invalid IAM Instance Profile name` on a parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault — re-run.

</details>
