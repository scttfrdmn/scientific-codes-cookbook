---
tool: pymatgen
tool_version: 2026.8.30
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
env_x86_64: dft-5cb0d94e928d
spawn_version: 0.115.0
last_verified: 2026-10-03
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# pymatgen — bulk-silicon structure, symmetry, and a CIF round-trip

pymatgen builds silicon from its space group and round-trips it through CIF — materials-informatics structure handling, no DFT. Phase diagrams, symmetry and matching over a whole Materials Project release are the [measured run](../../measurements/pymatgen-real/README.md).

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=pymatgen)" --wait
```

```python
from pymatgen.core import Structure, Lattice
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
s = Structure.from_spacegroup("Fd-3m", Lattice.cubic(5.43), ["Si"], [[0, 0, 0]])
SpacegroupAnalyzer(s).get_space_group_symbol()    # Fd-3m
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Si from `Fd-3m` + a cubic lattice | your own structure (from spacegroup, or a CIF) | Si is the hand-checkable case; the identities hold for any structure. |
| the reference density check | your material's known density | the density is externally anchored (Si ~2.329 g/cm³) — a physical reference, not a fitted band. |

Deterministic — **nothing is determinism scaffolding**. Note pymatgen's symmetry is **spglib-backed**, so its space group is a *reference* check against the known value, **not** an independent cross-engine check against [ase-phonopy](../ase-phonopy/README.md)'s spglib result (same engine). **Leave the fixture:** exact-by-construction identities don't get more legible with a bigger cell. Leave-it.

## Which box — [measured](../../measurements/pymatgen-real/README.md) on the whole Materials Project 2025-09-25 release

| chip | instance | tool wall | compute $ | **billed $/release** |
|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 1,499 s | 0.266 | 0.296 |
| Intel | `c8i.4xlarge` | 2,005 s | 0.418 | 0.447 |
| **AMD** | `c8a.4xlarge` | **1,070 s** | **0.256** | **0.283** |

The run covers all 58,315 phase diagrams, 210,579 space groups and the structure matching, with 16 workers. pymatgen reproduces MP's published e_above_hull, formation energy, stability and decomposition for every GGA and r2SCAN material to ~1e-12 eV/atom, and MP's space group for every structure. All three chips give identical answers. **AMD is fastest and 4% cheaper than Graviton4; Intel is slowest and ~50% dearer.** This is scalar Python, so per-core speed decides it, and the job needs only ~5.5 GiB.

## Shape, size, cost

One task, built in code with nothing staged: `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. Measured phase split for the verifying run: Docker install **40 s**, `dft` image pull **104 s**, the actual chemistry **4 s**. Provisioning is 97% of the 148 s wrapper window ([why](../../practices/what-this-does-not-cover.md)), so **these timings are not compute cost** — and this is the shape where packing or an AMI would help, if there were enough work to pack ([layout](../../patterns/layout-and-effective-cost.md)).

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

**x86_64** (`c8i.large`, the env's linux-64 twin `envs-x86_64/dft-5cb0d94e928d`): every row is
identical to Graviton4 — 8, Si, 5.4300, 2.3304, Fd-3m (#227), True. All are exact-by-construction
or symmetry results, so nothing is expected to differ across chips, and nothing does.

### Run + verify

```sh
make run RECIPE=pymatgen
make ls RECIPE=pymatgen
```

The smoke check runs *inside* the task, and the bucket listing is the second half of it — an exit code says the command ran, never that its output is real. Expect one object (`smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
