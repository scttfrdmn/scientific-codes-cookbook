---
tool: gemmi
tool_version: "0.7.5"
env: md
image: quay.io/aarchsci/md@sha256:285377124854316a3604756eecde350dfb45b26b24b9f0e4f247d3b0a0dff58f
gap_x86_64: the aarch.science `md` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-13) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-11
---
# gemmi — space groups against the International Tables

Checks crystallographic symmetry on Graviton4 against published space-group orders, a group axiom, and three independent structure parsers. For anyone handling mmCIF/PDB coordinates on ARM.

## Run it

```bash
make stage RECIPE=gemmi     # once: the checks only — the references are published tables
spawn task run --spec "$(make -s spec RECIPE=gemmi)" --wait
make ls RECIPE=gemmi

sg = gemmi.find_spacegroup_by_name("F m -3 m")
len(list(sg.operations()))               # 192 — the published order, not a measurement
gemmi.UnitCell(40.96, 18.65, 22.52, 90, 90, 90).volume   # == the closed form, exactly
gemmi.read_structure("structure.pdb")    # within half a %8.3f grid step of what was written
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| mmCIF for coordinates you keep | PDB | **PDB's field is `%8.3f`**, so it quantizes to 0.001 Å and loses up to **5e-4 Å**. Measured: `4.985e-04` through PDB versus `4.998e-08` through mmCIF. |
| the float32-ULP tolerance | exact equality across parsers | **don't.** biopython, MDAnalysis and MDTraj all store **float32** (`get_coord()` included), and MDTraj works in nm — so bit-equality with gemmi's float64 is impossible by dtype, not by error. |
| the synthetic 400-atom structure | a real PDB entry | nothing is fetched here. Real entries bring altlocs, insertion codes and multiple models, none of which this exercises. |
| `gemmi` from this env | — | it is now a **declared** member of `md` ([aarchsci#36](https://github.com/playgroundlogic/aarchsci/issues/36)); it used to arrive transitively. The run still records the resolved version to `gemmi-version.txt`. |
| symmetry only | structure-factor work | MTZ/reflection handling is untouched; this page is the symmetry and coordinate layer. |

**Leave the fixture.** Nothing is fetched and no structure is read: the references are published space-group orders, a closed-form volume, and closure under composition — none of which is data. **Scale it** by asserting more of the 230 space groups; the closure check costs O(n²) in the group order and `F m -3 m` already does 36,864 products.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All checks are **7 s** of a 1m39s window; 24 s is installing Docker and 46 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a published table, a group axiom with no reference value at all, two tolerances derived from a format and a dtype, and a control</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| gemmi / biopython / MDAnalysis / MDTraj | read from the running install | **0.7.5** / 1.88 / 2.10.0 / 1.11.2 |
| **12 space-group orders** | **== the International Tables** | **all 12 match** |
| **12 space-group numbers** | **== the International Tables** | all 12 match |
| **closure under composition** | **0 products outside the set** | **0 of 16 / 576 / 36,864** |
| **unit cell volume**, 3 lattices | **== the closed form exactly** | **0.000e+00** ×3 |
| **PDB round-trip** | **≤ 5.0e-04 Å** (half the `%8.3f` grid) | **4.985e-04** |
| **mmCIF round-trip** | ≤ 1.0e-06 Å | **4.998e-08** |
| **atom count**, 4 readers | **all equal** | **400** |
| **coordinates vs 3 referees** | **≤ one float32 ULP (3.815e-06)** | 1.892e-06 / 1.892e-06 / 2.327e-06 |
| control: 0.01 Å shift | must be caught | 1.000e-02 — **2621×** the ULP |
| determinism | two writes, one hash | byte-identical |

### A reference this code had no part in choosing

Most checks in this catalog have to build their reference — a conservation law, a sibling tool, a
convergence rate. Crystallography hands one over: the number of general-position equivalents in a
space group is **tabulated** in the International Tables for Crystallography, and gemmi's internal
tables either match or they don't.

```text
P 1          order   1        P 63/m m c   order  24
P -1         order   2        F d d d      order  32
P 21 21 21   order   4        I m -3 m     order  96
C 1 2/c 1    order   8        F m -3 m     order 192
```

Twelve groups spanning orders 1 to 192, all exact, with no data staged and no tolerance. The
space-group *numbers* are asserted alongside the orders, because a table could carry the right
count under the wrong index.

### The strongest check here needs no reference value at all

Symmetry operations form a **group**, so they must be closed under composition: for any two
operations `a` and `b`, the product `a·b` has to be another operation in the same set (modulo
lattice translations, hence the `wrap()`).

```text
P 21 21 21     4 ops       16 products     0 outside the set
P 63/m m c    24 ops      576 products     0 outside the set
F m -3 m     192 ops   36,864 products     0 outside the set
```

This is an axiom, not an observation — there is nothing to look up and nothing to tolerate. A
table with a wrong, missing or duplicated operation cannot satisfy it, and the recipe separately
asserts the triplets are distinct so a duplicate cannot hide inside the count. Same family as the
Euler characteristic in [paraview](../paraview/README.md): an integer the object must satisfy to be
what it claims.

### Both tolerances come from a format and a dtype, not from the run

**The PDB bound is arithmetic.** PDB writes coordinates as `%8.3f`, so representable values sit on
a 1e-3 Å grid and the worst possible rounding error is half a step: **5.0e-4 Å**. Observed
`4.985e-04` — just under a bound that was derived before the run, rather than a band drawn around
whatever came back. mmCIF carries more decimals and lands at `4.998e-08`.

**The parser bound is dtype.** Three independent parsers read the same file, and I expected
biopython to be exact because it looks like a float64 API. **It is not** — `get_coord()` returns
float32, measured, which was worth finding out before asserting anything:

```text
float32 ULP at |x| = 50 A        3.815e-06      <- the floor, set by representation
biopython   (float32 A)          1.892e-06
MDAnalysis  (float32 A)          1.892e-06
MDTraj      (float32 nm)         2.327e-06      <- the nm->A scaling costs a rounding
```

Every referee sits within one ULP of gemmi's float64. Demanding bit-equality would have failed on
representation rather than correctness — a method difference wearing a bug's clothes, which is the
thing [cross-checks](../../practices/cross-checks.md) exists to prevent. Atom counts, which no
dtype can excuse, are asserted exactly equal across all four readers.

**And the control proves the comparison is sensitive.** A 0.01 Å shift — invisible in any
rendering, and smaller than PDB's own precision claim is often assumed to be — shows up at
**2621× the ULP**. So the agreement above is not the comparison being too blunt to notice.

### Pins

| | |
|---|---|
| data | **none.** Published space-group orders, a closed-form volume, a group axiom; the 400-atom structure is generated from `default_rng(20261011)` |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/md@sha256:285377124854316a36…` — gemmi 0.7.5, biopython 1.88, MDAnalysis 2.10.0, MDTraj 1.11.2, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

**gemmi used to arrive here transitively, and no longer does.** When this recipe was verified it
was an undeclared dependency of `md`, which a re-solve could have dropped or moved with nothing
announcing it — so the run records the resolved version to `gemmi-version.txt`. [aarchsci#36](https://github.com/playgroundlogic/aarchsci/issues/36) has since declared it
explicitly and added it to that env's own import check, so the guarantee is now upstream rather
than detected after the fact. **The digest above is unchanged**: it is the image this recipe was
actually verified on, and repinning is a change to the input, not a text edit. Staging lifts the published table out of `identities.py` by name rather than restating
it, and checks both tolerance derivations without gemmi in the loop.

### Run + verify

```sh
make stage RECIPE=gemmi
spawn task run --spec "$(make -s spec RECIPE=gemmi)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/gemmi/r1/score.tsv" -
```

Fails on a pin mismatch, a space-group order or number that departs from the International Tables,
a single product falling outside its operation set, a cell volume that is not bit-identical to the
closed form, a PDB round-trip past half its coordinate grid, an atom count that differs between
readers, a referee further than one float32 ULP away, a control perturbation that is not caught, or
non-identical bytes from two writes — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Real structures.** No PDB entry is fetched, so altlocs, insertion codes, multiple models,
anisotropic B-factors, non-standard residues and the ligand/entity machinery are all untested —
and those are where real mmCIF parsing gets hard.

**Reflection data.** MTZ and structure factors, which are half of what gemmi is for, plus the
`gemmi sf2map`/`map2sf` paths. **Symmetry beyond the operation list**: reciprocal-space
asymmetric units, Wyckoff positions, and `gemmi.NearestImage`/contact searches. **Format
conversion at scale** — one 400-atom structure says nothing about a 10⁶-atom cryo-EM model.
**The 218 space groups not asserted**, and all non-standard settings. **Monomer libraries and
restraint handling**, used by refinement pipelines. **Superposition and RMSD**, where MDAnalysis
would be the natural cross-check and is not used that way here.

</details>
