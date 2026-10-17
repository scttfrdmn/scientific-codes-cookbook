---
tool: cctbx
tool_version: "2026.9"
env: structbio
image: quay.io/aarchsci/structbio@sha256:9fd80ad08baf5b1f79c0e433d0fb30eec1d2f810b34594ad556d8f4fb8bb5479
env_x86_64: structbio-e0104a505be2
spawn_version: 0.127.0
last_verified: 2026-10-11
last_verified_x86_64: 2026-10-16
---
# cctbx — all 230 space groups, cross-checked against a second implementation

Compares cctbx and gemmi on every space group in the International Tables, on Graviton4, then reads a real structure with both. For anyone doing crystallographic computing on ARM.

## Run it

```bash
make stage RECIPE=cctbx      # once: the checks only — the structure ships inside cctbx
spawn task run --spec "$(make -s spec RECIPE=cctbx)" --wait
make ls RECIPE=cctbx

sgtbx.space_group(sgtbx.space_group_symbols(225)).order_z()   # 192
len(list(gemmi.find_spacegroup_by_number(225).operations()))  # 192 — independently

uc = uctbx.unit_cell((10, 12, 14, 90, 102.5, 90))
uc.reciprocal().reciprocal().parameters() == uc.parameters()  # exactly
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| orthogonalising before comparing coordinates | a raw array diff | **cctbx is fractional, gemmi is Cartesian.** Diffing them directly compares a units mismatch, not two readings of the same file. |
| the shifted-index control | the agreement alone | **keep it.** 230/230 would also be scored by a comparison blind to group identity; shifting gemmi by one index breaks 66 of 230, which is what makes the agreement a result. |
| `1yjp.pdb` from the package | your own structure | nothing is staged: `iotbx` ships this for its own regression tests, so it is **version-matched to cctbx by construction**. Its sha256 is asserted, so a future cctbx changing the file fails loudly. |
| the primitive-lattice control | the absence rules alone | without it, "0 violations" might only mean the reflection generator never emits a forbidden index. On `P m -3 m`, **70 of 141** reflections have `h+k+l` odd. |
| space groups and cells | refinement | this is the symmetry and I/O layer. [gemmi](../gemmi/README.md) covers the same ground against the *published* tables; MTZ, maps and refinement are untouched. |

**Leave the fixture.** Nothing is fetched and nothing staged: the references are a complete crystallographic classification, two exact invariances, three parity rules, and a structure inside the installed package. **Scale it** by comparing more than `order_z` — the full operation sets, Wyckoff positions and asymmetric units are all reachable the same way.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All of it — 230 space-group comparisons, 847 reflections, a dual-parsed structure — is **5 s** of a 1m09s window; 38 s is installing Docker and 25 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: 230 of 230 space groups agreeing across two implementations, each cross-check with a control, and a structure that needs no staging</summary>

### The checks

| observable | assertion | observed | x86_64 (m8i.large) |
|---|---|---|---|
| staged bytes | match pin | 1 of 1 | identical |
| cctbx / gemmi / biopython / numpy | read from the running install | **2026.9** / 0.7.5 / 1.88 / 2.5.4 | identical |
| **space groups compared** | **== 230** | **230** | identical |
| **cctbx vs gemmi order** | **0 mismatches** | **0** | identical |
| distinct order values | — | 1,2,3,4,6,8,9,12,16,18,24,32,36,48,96,192 | identical |
| **control: gemmi index shifted by 1** | **must break** | **66 of 230 differ** | identical |
| `1yjp.pdb` sha256 | matches pin | `93ad953f…` | identical |
| **cell, cctbx vs gemmi** | **`== 0` exactly** | **0.000e+00** | identical |
| space group, both readers | identical | `P 1 21 1` | identical |
| atom count, both readers | identical | **66** | identical |
| **coordinates after orthogonalisation** | **< 1e-12 Å** | **1.776e-15** | identical |
| **reciprocal of reciprocal** | **`== 0` exactly**, 3 lattices | **0.000e+00** | identical |
| **cell volume, cctbx vs gemmi** | **`==` exactly**, 3 lattices | **0.000e+00** | identical |
| **d-spacing vs `a/√(h²+k²+l²)`** | < 1e-14 relative | **1.256e-16** | identical |
| **absences: I / F / C** | **0 violations** | 0 of 71 / 45 / 731 | identical |
| **control: primitive lattice** | forbidden indices must occur | **70 of 141** odd | identical |

### A second implementation, not a second look at the same one

[gemmi](../gemmi/README.md) checks gemmi's space-group orders against the International
Tables — a published authority. This is the other half of that claim: **cctbx, which shares no
code with gemmi, returns the same order for every one of the 230 groups.**

```text
space groups compared        230
mismatches                     0
distinct order values         16, from 1 (P 1) to 192 (F m -3 m)
```

Two independent C++ codebases, one a Boost.Python toolkit and one a modern library, agreeing on
a complete classification. That is the reason these two were requested in one env
([aarchsci#39](https://github.com/playgroundlogic/aarchsci/issues/39)) rather than separately.

**And the control is what turns 230/230 into a result.** A comparison that could not distinguish
the groups — one reading the wrong index, or defaulting — would score 230/230 too. Re-running it
with gemmi's index shifted by one breaks **66 of 230**:

```text
cctbx i  vs gemmi i      230 of 230 agree
cctbx i  vs gemmi i+1    164 of 230 agree   -> 66 differ
```

That 164 is not a failure: many adjacent groups genuinely share an order, which is exactly why the
control is reported rather than asserted at some threshold. What matters is that it is **not** 230.

### A real structure with nothing to stage

`iotbx` ships `1yjp.pdb` for its own regression tests, so the structure is **inside the installed
package** — version-matched to the library by construction, with no data tier and no fetch. Both
libraries read the same file:

```text
cell          21.937 4.866 23.477  90 107.08 90      max |diff| 0.000e+00
space group   P 1 21 1  /  P 1 21 1
atoms         66  /  66
coordinates   max |diff| 1.776e-15 A   (after orthogonalisation)
```

**The orthogonalisation is the load-bearing part.** cctbx stores fractional coordinates and gemmi
Cartesian, so diffing the two arrays directly compares different quantities and reports a units
mismatch as a disagreement — the shape of error that produced four false alarms in
[popgen](../popgen/README.md). Converting first makes it a real comparison, and 1.8e-15 Å on a
file whose own precision is 1e-3 Å means both parsers recover the same numbers to the last bit.

That the data is genuinely in the artifact was **checked by walking the installed file tree**, not
inferred from upstream having tests. The distinction is not academic: the same claim about
dftbplus was wrong — its suite is in the repo and zero test files reach the conda package
([aarchsci#37](https://github.com/playgroundlogic/aarchsci/issues/37)). The sha256 is asserted so
that a future cctbx revising the file fails as a changed input rather than passing quietly.

### Invariances and rules, with no reference value needed

```text
reciprocal(reciprocal(cell)) == cell        0.000e+00   orthorhombic, monoclinic, hexagonal
cell volume, cctbx vs gemmi                 0.000e+00   all three
d(hkl) vs a/sqrt(h^2+k^2+l^2)               1.256e-16   cubic, one ulp
```

Then the lattice centrings, where the space group *determines* which reflections cannot exist:

```text
I m -3 m    h+k+l even             71 reflections    0 violations
F m -3 m    h,k,l same parity      45 reflections    0 violations
C 1 2/m 1   h+k even              731 reflections    0 violations
CONTROL: P m -3 m                 141 reflections   70 have h+k+l ODD
```

**The primitive control is why the three zeros mean something.** If the reflection generator never
emitted a forbidden index, "0 violations" would be true and empty. On a primitive lattice the same
machinery produces 70 odd-sum reflections out of 141 — so the combinations are reachable, and the
centred lattices are actually excluding them. Staging separately confirms each rule allows
25–51% of a 729-index grid, so none of them is a predicate that forbids nothing or everything.

### Pins

| | |
|---|---|
| data | **none staged.** `1yjp.pdb` (18,134 B, sha256 `93ad953f…`) ships in `iotbx/regression/data`; references are the 230-group classification, two invariances and three parity rules |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/structbio@sha256:9fd80ad08baf5b1f79…` — cctbx-base 2026.9, gemmi 0.7.5, biopython 1.88, numpy 2.5.4, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

**x86_64.** The same spec with `ARCH=x86_64` runs on `m8i.large` through the `structbio-e0104a505be2`
twin (`envs-x86_64/`: the pinned image's versions as linux-64 builds, installed offline in 5 s).
cctbx-base, gemmi, biopython and numpy are the same versions; 4 versions differ (charset-normalizer,
fonttools, libglib, libsqlite), and linux-64's Python is split into `cpython`/`python-gil`
packages. `score.tsv` is **byte-identical** across chips, the 1.776e-15 Å orthogonalisation and
1.256e-16 d-spacing residuals included, and `1yjp.pdb` hashes the same out of both builds.

### Run + verify

```sh
make stage RECIPE=cctbx
spawn task run --spec "$(make -s spec RECIPE=cctbx)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/cctbx/r1/score.tsv" -
```

Fails on a pin mismatch, fewer than 230 groups compared, any space-group order where cctbx and
gemmi disagree, a shifted control that still agrees 230/230, a changed `1yjp.pdb` hash, a cell or
atom count that differs between readers, coordinates more than 1e-12 Å apart, a reciprocal
round-trip that is not exact, a volume disagreement, a d-spacing off the closed form, any centring
violation, or a primitive lattice that produces no forbidden index — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Everything cctbx is actually used for.** No structure-factor calculation, no refinement, no
maps, no MTZ: this is the symmetry, cell and coordinate layer only. `xray.structure` is
constructed from the PDB and then only its cell and sites are read.

**Diffraction-image processing is out of reach in this env**, not merely unexercised: `dxtbx`
3.30.0 and `reciprocalspaceship` 1.0.8 have **no `linux-aarch64` build at their latest version**,
so DIALS-style pipelines cannot be assembled here at all (recorded in
[aarchsci#39](https://github.com/playgroundlogic/aarchsci/issues/39) before the env was built).

**The operation sets themselves** — only `order_z` is compared across the 230 groups, not the
individual symmetry operations, which would be the stronger version of the same cross-check.
**Wyckoff positions, site symmetry and asymmetric units.** **Anomalous scattering and Friedel
pairs** — `anomalous_flag=False` throughout. **Non-standard settings and alternative origins**,
where cctbx's `space_group_info` handling is richer than anything tested here. **biopython**,
present in this env and untouched; it would be a third structure parser.

</details>
