---
tool: xtb
tool_version: "6.7.1"
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:f22ef7a3b0d6e506782596e297472fb355338502aaba668ba64d6e3a3cebe031
spawn_version: 0.123.0
last_verified: 2026-10-08
---
# xtb — GFN2 on water, against xtb's own committed test value

Reproduces the total energy and HOMO-LUMO gap that xtb's own unit test asserts, then checks the gradient against the molecule's symmetry. For anyone running semiempirical quantum chemistry on ARM.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=xtb)" --wait   # nothing to stage
make ls RECIPE=xtb

xtb coord --gfn 2 --etemp 300.0 --acc 1.0 --grad
#  | TOTAL ENERGY    -5.070451354837 Eh |   <- xtb's test asserts -5.070451355118
#  | HOMO-LUMO GAP   14.450365663200 eV |
```

**Nothing is staged.** The geometry and the reference values both come from xtb's test suite at the tag matching this image.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| Turbomole `coord` | your geometry | **`coord` is Bohr, `.xyz` is Ångström.** Converting units rounds, and that rounding moves the total energy at the 1e-7 level this recipe asserts. Keep `coord` for reference work. |
| `--gfn 2` | `--gfn 1`, `--gfn 0`, `--gfnff` | GFN2 is the default and the best general-purpose choice; GFN-FF is a force field, orders of magnitude faster and much less accurate. |
| single point | `--opt`, `--hess`, `--omd` | geometry optimisation, frequencies, dynamics. A non-zero gradient norm here is expected — this is a fixed geometry, not a minimum. |
| `--etemp 300 --acc 1.0` | tighter `--acc` | passed explicitly so the recipe does not depend on defaults staying put; they happen to be xtb's current defaults. |

**Leave the fixture.** Three atoms is the point: the energy is a *published* number in xtb's own repository, which no larger molecule here would give you, and C2v water makes the symmetry identity below available for free. **Scale it** to real molecules once the check passes — GFN2 handles hundreds of atoms in seconds.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 20m, cap $0.05. The SCF converges in 8 iterations in milliseconds; the recorded window is boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: a version-matched committed reference, a bit-exact symmetry identity, and one number that cannot be reproduced from the CLI</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| SCF convergence | converged | 8 iterations |
| **total energy** | **within 1.0e-7 of −5.070451355118** | **dev 2.810e-10** |
| **HOMO-LUMO gap** | **within 1.0e-4 of 14.450372368833 eV** | **dev 6.706e-06** |
| gradient norm | *reported, not asserted* — see below | 0.134752018160 |
| **gradient: out-of-plane** | **gy = 0 for all atoms** | **3.220e-18** |
| **gradient: C2 axis** | **gx(O) = 0** | **7.729e-18** |
| **gradient: mirror** | **gx(H1) + gx(H2) = 0** | **0.000e+00** |
| **gradient: mirror** | **gz(H1) − gz(H2) = 0** | **0.000e+00** |

**Both reference values and both tolerances are xtb's own**, from `test/unit/test_gfn2.f90` at
`v6.7.1` — the same version as the `xtb` in this env, which is load-bearing because a reference
from another release is a different number. The geometry is lines 78–81 of that file, verbatim.

The margins (356× and 15× inside) are consistent with the CLI's `1e-6 Eh` SCF threshold rather
than with anything marginal.

### The symmetry identity is bit-exact, and needs no reference

Water here is C2v in the xz plane: oxygen on the C2 axis, the hydrogens mirrored through x. So the
gradient *must* satisfy `gy = 0` everywhere, `gx(O) = 0`, `gx(H1) = −gx(H2)` and
`gz(H1) = gz(H2)`. The two mirror relations come back **exactly 0.000e+00** — bit-identical, not
merely small — and the two that involve summing different contributions land at machine epsilon
(~3e-18, ~8e-18).

This is the check worth copying, because it holds **whatever the reference values are**. A version
bump that moves the energies leaves it intact, and a build with a broken gradient fails it even
when the energy is right.

### The gradient norm cannot be reproduced from the CLI

`test_gfn2.f90` also pins `res%gnorm = 0.006457420125`. The CLI reports **0.134752018160** — a
factor of ~21 apart — and that is not a defect in either:

```text
total energy   agrees to 2.810e-10      <- geometry and method are right
HOMO-LUMO gap  agrees to 6.706e-06
gradient norm  differs by 1.283e-01
```

The energy agreement rules out geometry, method and parameterisation, leaving only that the two
numbers are **different quantities**: the unit test calls xtb's internal `scf()` driver directly,
while the CLI runs the full single-point driver. Asserting it would require widening the tolerance
20,000-fold, which would check nothing.

So it is reported with its provenance and the gradient is verified by symmetry instead. **If you
are trying to reproduce xtb's test values from the command line, the energies transfer and the
gradient norm does not.**

### Pins

| | |
|---|---|
| geometry + references | `grimme-lab/xtb` at tag `v6.7.1`, `test/unit/test_gfn2.f90` lines 78–81 and 140–150 |
| image | `quay.io/aarchsci/comp-chem@sha256:f22ef7a3…` — xtb 6.7.1 (`edcfbbe`), dftd4 4.2.0 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest. Lock `Built: 2026.10.08.144529` against a tag
pushed 14:47:34 the same day — same build, so the reference in git and the binary in the image are
the pair they claim to be.

### A note on the sibling cross-check

`dftd4 4.2.0` is in this env, and comparing xtb's internal D4 dispersion energy against the
standalone binary looks free. It is not like-for-like: `test_dftd4.f90` computes its reference with
`s9 = 0.0` (ATM three-body **off**) and Goedecker reference charges, which is not a default
`dftd4 --func` run. The GFN2 `e_disp` value is also unusable for it — that is D4 with
GFN2-specific damping. A real comparison has to match those parameters first
([cross-checks](../../practices/cross-checks.md)).

### Run + verify

```sh
spawn task run --spec "$(make -s spec RECIPE=xtb)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/xtb/r1/score.tsv" -
```

Fails on non-convergence, either energy outside xtb's own tolerance, or any broken symmetry
relation — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Geometry optimisation, frequencies, GFN-FF, solvation (`--alpb`, `--gbsa`), the other GFN
parameterisations, and the dftd4 comparison above. xtb's broader test suite covers metals and
mindless molecules that this recipe does not touch.

</details>
