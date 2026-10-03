# Reproduce a published number, don't just self-check

The strongest thing a recipe can prove is that the tool's output equals a number *someone else already computed and published* — not merely that the tool agrees with itself. A self-consistent band ("the energy is between −75 and −74 Ha") proves the code didn't crash; reproducing an external reference ("the energy is −74.963023 Ha, the value the maintainer published") proves the numerics are actually right on this hardware. Several recipes here do the second, and the sharpest form *manufactures* the reference from the code's own test suite. This page is the discipline; each recipe keeps its own number inline.

## Where an external reference comes from

In rough order of how much sourcing work each takes:

- **A textbook or literature value.** psi4's RHF/STO-3G energy of H₂ is −1.1167 Ha in every quantum-chemistry course; the geometry is three inline lines, nothing staged. When the answer is externally famous, just assert it.
- **The image builder's own published verification.** aarch.science runs a smoke calculation when it builds each env and publishes the figure. nwchem reproduces its `dft` D3 reference (−74.963023 Ha), gpaw reproduces −11.703689 eV, gromacs reproduces −9627.9 kJ/mol. The reference is a fact about *this image*, so the recipe checks value-for-value against what the builder saw.
- **A published data archive.** relion reproduces the depositors' own `postprocess.star` from the RODA `cryoem-spa-workflow-records-public` dataset — all 221 FSC shells × 8 columns at `max|diff| = 0`. The reference is someone else's *result*, pinned byte-for-byte.
- **The reference that travels *inside* the dataset — look here first, it is free.** Many scientific datasets ship derived quantities next to the raw bytes, computed by the publisher, and nobody checks them. A Sentinel-2 L2A STAC item carries ESA's own scene-classification percentages, so [earth-observation](../recipes/earth-observation/README.md) recomputes all eleven from the SCL pixels and matches to `9.6e-07` percentage points — on a class that is **3 pixels of 30,140,100**. A TIGER shapefile's `.dbf` carries Census's `ALAND`/`AWATER` beside each polygon, so [geo-ml](../recipes/geo-ml/README.md) recovers them geodesically to `6.7e-07` relative over 3,235 counties. This is the strongest form available, because the reference and the data are **the same pinned object** — there is no version to match and nothing to drift.
- **The code's own version-matched test suite — the manufacture move.** This is the one that takes real sourcing, and the one this page is named for.

## The manufacture move — and its invisible failure

**The symptom it solves:** a conda package strips the data the code needs to do anything real. conda-forge `siesta` ships no pseudopotentials; conda-forge `vina` ships no example receptor. A naive recipe can then only prove the binary *parses input* — it asserts nothing physical.

**Do this:** most scientific packages ship a test suite with committed reference outputs that never enter the conda build. Stage the pinned input from there, and "produce a number" becomes "reproduce a published number" for free:

- **SIESTA** stages `Tests/Pseudos/Si.psf` from `siesta-project/siesta` at tag `5.4.2` and reproduces `Tests/01.PseudoPotentials/Reference/psf.out`'s `Total = -214.377236 eV`.
- **Vina** stages the 1iep receptor/ligand from `AutoDock-Vina` at `v1.2.7` and reproduces the tutorial's docked pose, `-13.234` kcal/mol.
- **GDAL/PROJ** stages PROJ's `test/gie` files at tag `9.8.1` — the conda package ships the `gie` runner but not the tests — and reproduces **6,191** coordinates upstream committed as correct. Scale is a side benefit: a test suite is usually thousands of references, not one. Part of the sourcing is knowing which of them need data the package *also* strips: nine tests in `more_builtins.gie` want proj-data grids, so that file is deliberately left out rather than shipped as nine expected failures. A recipe that expects a red line teaches readers to ignore red lines.

**The load-bearing rule — and the failure nothing announces:** the staged input's version must match the binary's. A pseudopotential from a different SIESTA release, or a receptor prepared by a different Vina, is a *subtly wrong reference* — the run completes, converges, and reports a number, and nothing about it announces that the number is being checked against the wrong target. A wrong energy that looks like a right one is worse than a crash. So pin the test-suite file to the tag that matches the image, and say in the recipe that the match is load-bearing.

Staging a pinned file to S3 is the cookbook's normal model, **not** runtime-fetching — it's allowed exactly where an env's build-time constraints forbid bundling the data. Verify the file's sha256 on the box before the tool runs.

---

Lots of scientific packages ship tests with reference outputs that never make the conda build. Reach for one before settling for an init-only "it parses" check or a bare band around a hunch — a pinned reference is usually a tag away.
