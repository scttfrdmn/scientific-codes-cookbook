# pymatgen — bulk-silicon structure, symmetry, and a CIF round-trip

One task. pymatgen builds silicon from its space group and the smoke check confirms exact
structural identities — formula, site count, lattice, density, space group — plus a
Structure → CIF → Structure round-trip.

> **What this recipe does and does not cover.** It constructs one crystal and checks its
> exact properties and a format round-trip — enough to prove pymatgen's core structure and
> symmetry machinery works on Graviton4. Not a benchmark; no materials-database query,
> phase diagram, or DFT.

## Exact identities, one reference, one decode round-trip

- **Structural identities (exact by construction).** From `Fd-3m` + a cubic lattice,
  pymatgen builds the 8-site conventional diamond cell: reduced formula `Si`, lattice
  a = 5.43 Å.
- **Density (a physical reference).** 2.3304 g/cm³, computed from mass/volume — and it
  matches silicon's known density (~2.329 g/cm³). Exact-or-wrong and externally anchored.
- **Space group (reference, not a second engine).** `SpacegroupAnalyzer` returns `Fd-3m`
  (#227), the known value. Stated plainly: pymatgen's symmetry path is **spglib-backed**,
  so this is a reference check against the known space group, **not** an independent
  cross-engine check against `recipes/ase-phonopy`'s spglib result (it's the same engine).
- **CIF round-trip (decode statistic).** Structure → CIF → Structure recovers the site
  count, formula and lattice — the decode-statistic move on a crystal-structure text
  format, alongside the raster (GDAL), point-cloud (PDAL) and trajectory (MDTraj) instances.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` |
| | tag `2026.09.04` / `s5cb0d94e928d`, pymatgen (+ spglib, phonopy, ASE, gpaw, …), cosign-signed, `linux/arm64` |
| input | Si space group + lattice, **in code** — nothing staged |

**Data tier: none / in-task.** pymatgen ships in the `dft` env (with spglib/phonopy), not
comp-chem; same image as `recipes/gpaw`, `recipes/nwchem`, `recipes/ase-phonopy`.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| sites | exactly 8 (conventional diamond cell) | 8 |
| reduced formula | `Si` | Si |
| lattice a | 5.43 Å (by construction) | 5.4300 |
| **density** | 2.3304 g/cm³ (Si known ~2.329) | 2.3304 |
| space group | `Fd-3m` (#227), reference (spglib-backed) | Fd-3m (#227) |
| **CIF round-trip** | sites + formula + lattice recovered | True |

No fitted bands — the structural values are exact by construction, the density is a
physical reference, and the round-trip is exact.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The build +
symmetry + round-trip is **sub-second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.87 GB** `dft` image are the whole task. The recorded run's command window was **87s**
(02:59:18 → 03:00:45 UTC), all six checks passing (8 sites, density 2.3304 g/cm³, Fd-3m
#227, CIF round-trip recovered). TTL was **retightened from that first real run**: 10m →
**5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius, not caution; the
recorded run used the original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — the structure is built in code.

```sh
spawn task run --spec recipes/pymatgen/01-structure.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/pymatgen/r1/
```

The smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect one object (`smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
