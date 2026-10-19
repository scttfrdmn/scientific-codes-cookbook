# pymatgen over a whole Materials Project release: Graviton4, Intel and AMD

> **pymatgen rebuilds all 58,315 of the release's phase diagrams and reproduces MP's published
> e_above_hull, formation energy, stability and decomposition for all 235,831 thermo docs to
> ~1e-12 eV/atom. It also reproduces MP's space group for all 210,579 structures.** The run takes
> 18–33 minutes on 16 vCPUs. AMD is fastest and cheapest per release ($0.28 billed), Graviton4
> is 4% dearer, and Intel is 1.9x slower than AMD and 58% dearer. All three chips give the same
> answers.

Workload: the **complete** Materials Project `2025-09-25` release on RODA. That is every
document of its `summary` collection (210,579 materials, each with structure and MP's symmetry)
and of its `thermo` collection (400,163 docs, each with its computed entry and MP's hull
results). pymatgen does three jobs a materials-informatics user runs:

- **Phase diagrams** for the `GGA_GGA+U` (153,167 materials) and `R2SCAN` (82,664) thermo types.
  Every maximal chemical system gets one `PhaseDiagram`, built from the entries MP publishes
  (27,032 + 31,283 diagrams, up to 9 elements and 5,022 entries). Then each material gets
  `get_decomp_and_e_above_hull`, `get_form_energy_per_atom`, `get_phase_separation_energy` and
  `get_equilibrium_reaction_energy`.
- **Symmetry**: `SpacegroupAnalyzer` at MP's own `symprec=0.1, angle_tolerance=5` on every
  structure.
- **Structure matching**: `StructureMatcher` at MP's materials-builder settings (`ltol 0.2,
  stol 0.3, angle_tol 5`, primitive cell, scale, `ElementComparator`). It runs on every structure
  against its own transformed copy, and within each of the 10,181 formula + space-group groups
  that hold more than one MP material.

## Run it

```sh
B=$(make -s print-bucket)
measurements/pymatgen-real/stage-inputs.sh "$B"     # RODA -> your bucket, S3-to-S3, 1.04 GB
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py pymatgen $a $B r3 measurements/pymatgen-real/real.task.json)" --wait
done
python3 measurements/pymatgen-real/collect.py <dir holding each chip's untarred out/ as arm64/ x86_64/ amd/>
```

`real.py` is ordinary pymatgen: `PhaseDiagram([PDEntry(comp, energy)...])` per chemical system,
fanned over a `multiprocessing.Pool` of 16 with `OPENBLAS_NUM_THREADS=1`.

## Results

16 workers on a `4xlarge` (16 vCPU, 32 GiB) of each chip, the same `dft` env (aarch.science arm64;
its linux-64 twin on both x86 chips), the same staged bytes:

| chip | instance | tool wall | billed | peak mem | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 1,499 s | 1,668 s | 5.5 GiB | 0.6381 | 0.266 | 0.296 |
| Intel (Xeon 6975P-C) | `c8i.4xlarge` | 2,005 s | 2,147 s | 10.1 GiB | 0.7497 | 0.418 | 0.447 |
| **AMD (EPYC 9R45)** | `c8a.4xlarge` | **1,070 s** | **1,181 s** | 10.0 GiB | 0.8622 | **0.256** | **0.283** |

A *result* is the whole release analysed: every phase diagram, symmetry and match. *Tool wall*
is `real.py`, and *billed* is EC2 launch to terminate.

- **Phase-diagram work is ~56% of the run** (844 / 1,083 / 555 s). Symmetry plus self-matching
  takes 538 / 744 / 411 s, and grouping 107 / 167 / 95 s. The biggest single diagram (6
  elements, 4,883 entries) takes 109 s on Graviton4, but 58,315 diagrams balance well over 16
  workers (15.2 cores busy on average).
- **AMD is 1.40x faster than Graviton4 and 4% cheaper per release. Intel is 1.34x slower than
  Graviton4.** This is a pure-Python, scalar workload (qhull, spglib, numpy on small arrays), and
  per-core speed decides it. Intel's 16 vCPUs are 8 cores × 2 threads (`Thread(s) per core: 2`).
  AMD's and Graviton4's are 16 full cores. So the rental is like-for-like, but a per-core
  comparison it is not.
- **Peak memory** is cgroup `memory.peak`. On x86 the env is installed into the container at
  start, and the cgroup counts that page cache. The job itself needs ~5.5 GiB, so any 16-vCPU
  `c` box fits it.
- n = 1 per chip. Every output is deterministic: the per-material tables are identical across
  chips (below), so the timings are of one computation.

## Why this size

This is the whole of the current release, which is the unit a materials-informatics user works
on. A user rebuilds hulls with new entries against all of MP, screens stability across every
chemical system, or deduplicates structures against the database. A slice would cut chemical
systems out of their own phase diagrams and change the answers. The scale also earns its keep
here. The 13 StructureMatcher failures (of 210,579) and the single inconsistent MP doc (of
400,163) below are events that no fixture would contain.

## What held at scale, on all three chips

| assertion | bound | observed (Graviton4; x86 the same) |
|---|---|---|
| decode: `nsites`, reduced composition, volume vs MP | exact; volume 1e-9 rel | 210,579 / 210,579, 2.1e-15 |
| space group == MP's (spglib 2.7.0 here, 2.5/2.6 at MP) | all | **210,579 / 210,579** |
| space group invariant under a rigid rotation | all | 210,579 / 210,579 |
| `StructureMatcher(primitive_cell=False)` fits its own rotated copy | all | 210,579 / 210,579 |
| e_above_hull vs MP, both thermo types | 1e-9 eV/atom | ≤ 1.4e-12 |
| formation energy vs MP | 1e-9 eV/atom | ≤ 1.4e-14 |
| decomposition products (set) vs MP / amounts | exact / 1e-9 | 0 mismatches / ≤ 5.7e-14 |
| decomposition enthalpy, exact path vs MP | 1e-9 eV/atom | ≤ 1.4e-12 |
| decomposition enthalpy, SLSQP path vs MP | 1e-6 eV/atom | ≤ 3.5e-7 (`mp-27007`) |
| equilibrium reaction energy (stable entries) vs MP | 1e-9 eV/atom | ≤ 4.3e-13 |
| `is_stable` vs MP | 0 flips | 0 (+126 explained, below) |
| **chemical-potential shift** (a per-element μ added to every energy) leaves e_above_hull and formation energy unchanged | 1e-9 | ≤ 4.6e-13 |
| an entry gets the same e_above_hull in every phase diagram that contains it | 1e-9 | ≤ 1.4e-12 |
| decomposition mass balance; `E − Σ aᵢEᵢ == e_above_hull` | 1e-9 | ≤ 6.3e-14 |
| **every per-material number vs Graviton4** (`collect.py`) | 1e-9; SLSQP 1e-6 | Intel and AMD ≤ 9.1e-13; SLSQP ≤ 5.7e-11 |

The shift invariance holds because a hull distance is unchanged by any affine change of the
elemental reference energies. An implementation that measured distance to the wrong facet, or
that normalised per formula unit instead of per atom, could not satisfy it.

The SLSQP bound has a reason behind it. For 50,694 GGA and 70,373 r2SCAN materials, a stable
entry of the same composition exists. For those, pymatgen's `get_phase_separation_energy`
minimises over competing phases with SLSQP (`ftol 1e-8`) instead of reading the hull. The answer
is then an optimiser's convergence on an objective of order 10 eV/atom, not an exact facet
distance.

**Every disagreement with MP is explained:**

- **`mp-15590` (GGA_GGA+U)**: MP's published `energy_per_atom` is 1.12e-5 eV/atom off its own
  entry's corrected energy. It is the only doc of the 400,163 that is inconsistent this way. Our
  e_above_hull and formation energy differ from MP's by exactly that discrepancy (residual 1.3e-16).
- **126 R2SCAN materials**: MP marks them `is_stable: false`, yet publishes `energy_above_hull: 0`
  and a *negative* decomposition enthalpy. Both of those say the material is on the hull, and
  pymatgen agrees with those two numbers to 1e-12. The flag contradicts MP's own values. All 126 have ids at
  or above `mp-3200000`.
- `formula_pretty` for `mp-1019741` and `mp-1019742` is `HeSiO2`, where pymatgen writes `SiO2He`.
  The composition is identical; only the order is display convention.

## What is reported, not asserted

- **The space group depends on site order and origin at MP's tolerance.** A rotation never changes
  it. But permuting the sites changes it for 494 of 210,579 structures (371 higher, 123 lower), and
  shifting the origin changes it for 33. At `symprec=0.1`, "equivalent within tolerance" is not
  transitive, so a
  borderline structure can resolve to a different group depending on where the search starts.
  The changed groups are **identical on all
  three chips**: this is a property of the algorithm, not of floating point.
- **StructureMatcher at MP's settings fails to match 13 structures with their own rotated copy.**
  With `primitive_cell=False` all 210,579 match, and the primitive cells found for the original
  and the rotated copy have the same site count. So the failure lies in the primitive-cell
  path's lattice match, not in the atoms. The 13 are `mp-687301, mp-694999, mp-722602, mp-1237669, mp-1273133,
  mp-1291152, mp-1297766, mp-1305634, mp-774032, mp-907483, mp-1178332, mp-2719870, mp-28688`.
  The full rotate + shift + permute + 1.1x volume copy fails 78.
- **270 groups covering 541 distinct MP materials** are merged by StructureMatcher at MP's own
  builder settings (`results/matches.tsv`, identical on all chips). In 129 groups every id is
  `mp-3000000` or above, in 138 every id is below it, and 3 groups mix the two.
- The `GGA_GGA+U_R2SCAN` mixed thermo type (164,332 docs, MP's default) is not rebuilt.
  Rebuilding it needs MP's `MaterialsProjectDFTMixingScheme` over the full GGA and r2SCAN entries.

## Fixed point or path

Every quantity here is a fixed point or an exact discrete answer, and all of them agree across
chips. Space groups, matches, stability flags and decomposition product sets are identical, row
for row. Hull numbers agree with Graviton4 to ≤ 9.1e-13 eV/atom on both Intel and AMD (the same
worst case on both). The SLSQP decomposition enthalpies agree to ≤ 5.7e-11, which is the
optimiser landing on the same minimum with different last bits. OpenBLAS reports `neoversev2` on
Graviton4 and `Cooperlake` on both x86 chips, but at these matrix sizes it does not reach a
visible digit.

## Pins

| | |
|---|---|
| input | Materials Project `2025-09-25` on RODA, `s3://materialsproject-build/collections/2025-09-25/{summary,thermo}/` (21 + 2,917 `jsonl.gz`, 1,040,234,151 bytes), copied S3-to-S3 by `stage-inputs.sh` and pinned file by file in `inputs.sha256`, re-checked in-task |
| image | arm64 `quay.io/aarchsci/dft@sha256:0740fab9…` (the recipe's pin); x86 the env's linux-64 twin `envs-x86_64/dft-5cb0d94e928d` |
| versions (all chips) | pymatgen 2026.8.30 (`pymatgen.core.__version__`), spglib 2.7.0, numpy 2.5.2, scipy 1.18.0 |
| scripts | `run.sh`, `real.py`, `inputs.sha256`, each pinned by sha256 in the spec (`mkspec.py` writes it); `collect.py` runs locally on the three outputs |

**Data tier: RODA.** The `2025-09-25` prefix is a dated release, but its summary objects carry a
2025-12-17 modification time, so the prefix alone does not pin the bytes. The sha256 manifest
does. The per-chip `check.txt`, `host.txt` and `footprint.txt` are in `results/`, with the
cross-chip comparison in `results/crosschip.txt`. The per-material TSVs (`hull-*.tsv`,
`symmetry.tsv`, `phase-diagrams.tsv`) are in each run's `pymatgen-real-out.tar.gz` in the bucket.
