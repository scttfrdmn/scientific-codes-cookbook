---
tool: pymatgen
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.104.0
---
# pymatgen — bulk-silicon structure, symmetry, and a CIF round-trip

pymatgen builds silicon from its space group; the checks are exact structural identities — formula, site count, lattice, density, space group — plus a Structure → CIF → Structure round-trip.

> **What this covers.** Construct one crystal and check its exact properties and a format round-trip — proof pymatgen's core structure and symmetry machinery works on Graviton4. Not a benchmark; no materials-database query, phase diagram, or DFT.

## Run it

```python
from pymatgen.core import Structure, Lattice
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
s = Structure.from_spacegroup("Fd-3m", Lattice.cubic(5.43), ["Si"], [[0, 0, 0]])
SpacegroupAnalyzer(s).get_space_group_symbol()    # Fd-3m
```

One task, built in code — nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Si from `Fd-3m` + a cubic lattice | your own structure (from spacegroup, or a CIF) | Si is the hand-checkable case; the identities hold for any structure. |
| the reference density check | your material's known density | the density is externally anchored (Si ~2.329 g/cm³) — a physical reference, not a fitted band. |

Deterministic — **nothing is determinism scaffolding**. Note pymatgen's symmetry is **spglib-backed**, so its space group is a *reference* check against the known value, **not** an independent cross-engine check against [ase-phonopy](../ase-phonopy/README.md)'s spglib result (same engine). **Leave the fixture:** exact-by-construction identities don't get more legible with a bigger cell. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Build + symmetry + round-trip is sub-second. Recorded command window **87s** — boot, Docker install, and the ~0.87 GB `dft` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the identities, pins, smoke-check table, run + verify</summary>

### The checks — exact identities, one reference, one decode round-trip

- **Structural identities (exact by construction).** From `Fd-3m` + a cubic lattice, pymatgen builds the 8-site conventional diamond cell: reduced formula `Si`, a = 5.43 Å.
- **Density (a physical reference).** 2.3304 g/cm³ from mass/volume, matching silicon's known ~2.329 — exact-or-wrong and externally anchored.
- **CIF round-trip (decode statistic).** Structure → CIF → Structure recovers the site count, formula and lattice — the decode-statistic move on a crystal-structure text format.

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` (tag `2026.09.04` / `s5cb0d94e928d`, pymatgen + spglib + phonopy + ASE + gpaw + …, cosign-signed, `linux/arm64`) |
| input | Si space group + lattice, in code — nothing staged |

pymatgen ships in the `dft` env; same image as [gpaw](../gpaw/README.md), [nwchem](../nwchem/README.md), [ase-phonopy](../ase-phonopy/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| sites | exactly 8 (conventional diamond cell) | 8 | wrong build |
| reduced formula | `Si` | Si | wrong composition |
| lattice a | 5.43 Å (by construction) | 5.4300 | wrong lattice |
| **density** | 2.3304 g/cm³ (Si known ~2.329) | 2.3304 | wrong mass/volume |
| space group | `Fd-3m` (#227), reference (spglib-backed) | Fd-3m (#227) | broken symmetry |
| **CIF round-trip** | sites + formula + lattice recovered | True | writer/reader mismatch |

No fitted bands — structural values exact by construction, density a physical reference, round-trip exact.

### Run + verify

```sh
spawn task run --spec recipes/pymatgen/01-structure.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/pymatgen/r1/
```

The smoke check runs *inside* the task, and the bucket listing is the second half of it (spore-host/spawn#561). Expect one object (`smoke-check.txt`). Re-run: bump the `-r1` suffix.

</details>
