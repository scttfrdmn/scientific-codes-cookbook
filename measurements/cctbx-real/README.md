# cctbx on a real workload: deposited R-factors, recomputed from the PDB's own files

> **167 PDB entries rescored from their deposited model and structure factors, on three chips, in
> 3–5 minutes for 6–8 cents.** The median recomputed R-work is 0.0065 from the deposited value.
> Intel and AMD agree bit for bit. Graviton4 differs by up to 3e-4 in R, because scaling is an
> optimisation path that ends at a slightly different point.

Workload: **every entry in hash directory `ab` of the RCSB 2026-01-01 snapshot that deposited
structure factors**. That is 181 entries, with models and data, read from RODA (`s3://pdbsnapshots`).
For each one, `mmtbx.model_vs_data` runs through its own `run()` against the deposited mmCIF and the
first data block of the deposited `sf.cif`. That is the tool's own way of answering "does this model
fit its data?".

**Why this size.** Rescoring deposited entries is how cctbx is used at archive scale. The
model_vs_data paper ([Afonine et al. 2010](https://doi.org/10.1107/S0021889810015608)) did it across
the whole PDB. PDB-REDO does the same before it re-refines. A hash directory is a slice of the archive
chosen by ID, not by property. It runs from `1ab0` to `8abx`, so it spans IDs issued from the 1990s to the 2020s. It covers 30 space
groups, 0.89–3.35 Å, 554–31,975 atoms and 1,799–291,546 reflections, 9.0 M reflections in all. A
whole snapshot is 1,056 directories. At this rate that is ~72 box-hours (~$45 of compute on
Graviton4), a sweep rather than a measurement.

## Run it

```sh
bash stage-inputs.sh "$COOKBOOK_BUCKET"          # S3-to-S3 from RODA; verifies cohort.sha256
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 ../../scripts/spec_arch.py cctbx $a "$COOKBOOK_BUCKET" "$(date +%s)" real.task.json)"
done
python3 compare.py results/arm64 results/x86_64 results/amd
```

## Results

`c8g/c8i/c8a.4xlarge` (16 vCPU, 32 GiB). 16 worker processes, one entry each, with
`OPENBLAS_NUM_THREADS=1`. Same staged bytes on every chip.

| chip | instance | cores × threads | tool wall | billed | peak RSS (worker / box) | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 16 × 1 | 245 s | 349 s | 922 MiB / 5.9 GiB | 0.6381 | 0.043 | 0.062 |
| Xeon 6975P-C | `c8i.4xlarge` | 8 × 2 | 285 s | 406 s | 835 MiB / 7.3 GiB | 0.7497 | 0.059 | 0.085 |
| **EPYC 9R45** | `c8a.4xlarge` | 16 × 1 | **188 s** | **282 s** | 917 MiB / 7.4 GiB | 0.8622 | 0.045 | 0.068 |

A *result* is the whole cohort: 181 entries classified, 167 rescored and every check passed. That is
about $0.0004 per structure. *Tool wall* is `real.py`, start to finish. *Billed* is EC2 launch to
termination at spawn's printed price. *Worker* is the largest single entry's `ru_maxrss`. *Box* is the
cgroup's `memory.peak`, which on x86 also counts the env install.

- **Graviton4 is cheapest per result.** AMD finishes first: 1.30× faster than Graviton4 and 1.52× faster
  than Intel. But AMD's hour costs 35% more than Graviton4's, so it comes out 10% dearer billed.
  Intel's 16 vCPUs are 8 cores, and it is the slowest and dearest.
- **The tail sets the wall.** Summed per-entry time is 3,209 s on Graviton4, so the 16 workers are
  82% busy. The rest is waiting on the largest entries, up to 110 s each. Sorting entries by
  reflection count would recover most of that.
- **Boot and staging are 94–121 s of each bill**, about 30%. Staging is 320 MB in 362 objects, and
  Intel also installs the env twin. These timings are not compute cost in general
  ([layout](../../patterns/layout-and-effective-cost.md)).
- **n = 1 per cell.** Intel and AMD give byte-identical results, so a repeat would measure only timing
  noise.

## What held at scale, on all three chips

Checked inside each run by `real.py`. A failure fails the task.

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| every cohort entry classified | rescored, skipped or failed with its reason, nothing lost | 181 / 181 | 181 / 181 | 181 / 181 |
| **recomputed R reproduces the deposited R** | published truth, from the entry's own `_refine` | median \|ΔR-work\| **0.0065** | 0.0065 | 0.0065 |
| **permuted-Fobs control is worse** | the same fmodel against shuffled amplitudes must lose the fit, or R ≈ 0.2 says nothing about the model | 167 / 167, min margin 0.265 | 167 / 167 | 167 / 167 |
| **P1 expansion reproduces F_calc** | the symmetry operators, applied by cctbx, must equal an explicit P1 sum | max 4.0e-14 relative | 4.0e-14 | 4.0e-14 |
| **gemmi F_calc == cctbx F_calc** | a second code on the same atoms and reflections, both IT92 | max **1.5e-7** relative | 1.5e-7 | 1.5e-7 |
| both readers build the same atoms | the cross-check compares one structure | 167 / 167 | 167 / 167 | 167 / 167 |
| model_vs_data scored the deposited atoms | the scored structure equals the file's own hierarchy, and R at fixed scales is unchanged | 0 differ, ΔR 0 | 0 | 0 |
| R-work, R-free, control finite, in (0, 1) | NaN passes every one-sided bound | 167 / 167 | 167 / 167 | 167 / 167 |

<details>
<summary>How close to deposited, what failed and why, the cross-code tolerance, and what differs across chips</summary>

### How close recomputed R is to deposited R

```text
|R-work recomputed - deposited|  167 entries   median 0.0065   <=0.01: 117   <=0.02: 150   <=0.05: 162   max 0.138
|R-free recomputed - deposited|  152 entries   median 0.0057   <=0.01: 111   <=0.02: 129   <=0.05: 145   max 0.124
```

The assertion is on the median (< 0.01), not per entry. Recomputing an R-factor without the
depositor's program, scattering table, solvent model and TLS is not expected to reproduce every entry.
The paper reports "a number of outliers where the recomputed R values are significantly different",
and so does this cohort. The worst are `3abg` (0.158 deposited, 0.296 recomputed) and `1ab1`
(0.147 → 0.238). `1ab1` is 0.89 Å with no free set, the kind of subatomic-resolution case the paper
discusses. The six worst are listed in [`results/compare.txt`](results/compare.txt). They are
reported, not tuned away.

The **control** is what makes a 0.0065 median mean something. With Fobs permuted among reflections
(seeded), the same model, scaling and bulk solvent give R-work 0.49–0.81, median 0.695. Wilson's
random-structure value is 0.586 acentric and 0.828 centric. So an R near 0.2 was earned by the model.

### What did not run, and why (14 of 181, the same 14 on every chip)

| reason | entries | what it is |
|---|---|---|
| mmtbx rejects the NCS operators | `4ab7 4abn 4aby 5ab4 5ab6 5abj 5abm` | `iotbx.mtrix_biomt.validate` requires each matrix to be a rotation within 1e-4 rms, and rejects all seven. The worst deposited operator in each has rms(RᵀR − I) of 6e-5–4e-4 and a determinant of 0.99981–1.00014 |
| model and data disagree on symmetry | `2abr 4abj 5ab3` | cells rounded differently between the files (90.6 vs 90.62 Å), and `5ab3`'s model says P 3₂ 2 1 while its data say P 3₁ 2 1: the enantiomorph |
| inconsistent symmetry operators inside the sf.cif | `5abg` | the reflection file's own operator list contradicts itself |
| unknown element `X` | `3abk 3abl 3abm` | `UNX` atoms, which model_vs_data refuses rather than guess |

These are archive-data issues that the tool reports by name. Where a first data block carries more
than one observation array (39 entries) or flag array (55), model_vs_data stops rather than guess. The
run then chooses in a fixed order, recorded per entry: `F_meas_au` > `intensity_meas` > anomalous
pairs, and `status` > `pdbx_r_free_flag`. 15 entries deposited no free set and are scored on R-work
only.

### The cross-code tolerance: why 1e-6

Gemmi and cctbx share no code. On every rescored entry, the first 40 working reflections are computed
by direct summation on both sides, with both using the IT92 four-Gaussian table on neutral atoms:
gemmi `StructureFactorCalculatorX` and cctbx `it1992`. The bound is set by gemmi's Python binding,
which instantiates `IT92<float>` and evaluates `sin²θ/λ²` in float, so ~1e-7 relative is the floor.
The observed max is 1.5e-7 and the median 3.7e-8. Two conditions make it like with like. Both sides
read the **model file's** cell, because model_vs_data takes the cell from the reflection file, and in
32 entries those differ by up to 0.005 Å. And charges are stripped, because gemmi's IT92 ignores formal
charge by default.

**A cctbx sharp edge, measured here:** `copy.deepcopy` of an `iotbx.pdb` hierarchy round-trips
through PDB format whenever the model fits it. That rounds coordinates to 0.001 Å, B to 0.01 and U to
1e-4. So `mmtbx.model.manager(model_input=...)` returns a hierarchy at PDB precision for an mmCIF
deposited at higher precision. On `8abo`, 3.18593 became 3.186. model_vs_data's scored structure is
**not** affected: the run asserts it equals the file's own atoms on all 167 entries, with ΔR exactly 0
at fixed scales. But a script that builds its own model manager computes on rounded atoms. On this cohort that
moves F_calc by up to 2e-4 relative.

### Across chips: what is a fixed point and what is a path

| quantity | Intel vs Graviton4 | AMD vs Graviton4 | Intel vs AMD |
|---|---|---|---|
| entries, status, reason | identical | identical | identical |
| atoms, free-set size | identical | identical | identical |
| reflections kept after outlier rejection | 1 entry differs by 1 (`5ab0`: 88,242 vs 88,243) | same | identical |
| R-work | 15 / 167 bit-identical, max \|Δ\| 2.9e-4 | same | **bit-identical** |
| R-free | 22 / 167, max 6.6e-4 | same | **bit-identical** |
| permuted-Fobs control R | 31 / 167, max 4.4e-3 | same | **bit-identical** |
| P1-expansion residual | max \|Δ\| 1.2e-14 | same | **bit-identical** |
| median \|ΔR-work\| vs deposited | 0.0065 | 0.0065 | identical |

**F_calc is a fixed point. R after `update_all_scales` is a path.** Structure-factor summation
agrees to rounding on every chip, with the P1 residual within 1.2e-14. The scaling is an iterative
fit: bulk-solvent grid search, anisotropic scale and outlier rejection. Its last bits differ, so it
lands up to 3e-4 apart in R-work and, once, keeps one more reflection. That is three orders of
magnitude below the 0.0065 median distance from the deposited value, so no assertion is near it. The
control's larger spread (4.4e-3) is the same fit on data with no signal, where the optimum is flat.
The two x86 chips run one build with the same OpenBLAS kernel (`Cooperlake`) and agree bit for bit.
Graviton4 reports `neoversev2`.

### Pins

| | |
|---|---|
| data | RODA `s3://pdbsnapshots/20260101/pub/pdb/data/structures/divided/{mmCIF,structure_factors}/ab/`, 181 entries × (model + sf). The snapshot is dated and frozen. Copied server-side with S3's SHA-256 checksum, pinned in [`cohort.sha256`](cohort.sha256) (362 files), and re-checked at stage-in |
| scripts | `real.py`, `real.sh`, pinned in `scripts.sha256` |
| image | the recipe's `quay.io/aarchsci/structbio@sha256:9fd80ad0…` (cctbx-base 2026.9, gemmi 0.7.5). x86 runs use its `structbio-e0104a505be2` twin, with the same cctbx and gemmi versions |

If you also rescore these entries with [gemmi](../../recipes/gemmi/README.md), point it at the same
`pdb-20260101/ab/` objects. Cross-code agreement only means something on identical bytes.

### Not covered

Refinement, maps, twinning (`twin_law` unset), and neutron or joint X-ray/neutron entries (none in this
directory). Also not covered: TLS reconstruction beyond the ADPs in the file, and the 1,055 other hash
directories.

</details>
