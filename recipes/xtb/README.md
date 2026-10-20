---
tool: xtb
tool_version: "6.7.1"
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:f22ef7a3b0d6e506782596e297472fb355338502aaba668ba64d6e3a3cebe031
env_x86_64: comp-chem-823f1ef5e11e
spawn_version: 0.131.0
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# xtb — GFN2 on water, against xtb's own committed test values

Reproduces the total energy, gradient norm and HOMO-LUMO gap that xtb's own unit test asserts, on x86. **GFN2 energies are right on every chip; its gradient is not.** It is wrong on every structure on Graviton ([xtb-feedstock#50](https://github.com/conda-forge/xtb-feedstock/issues/50)) and on 5.6% of GMTKN55 on x86 ([measured](../../measurements/xtb-real/README.md)). Don't trust `--opt`, `--hess` or `--omd` yet.

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
| single point | `--opt`, `--hess`, `--omd` | geometry optimisation, frequencies, dynamics — all built on the gradient, which is **wrong on aarch64 everywhere and on x86 for 133 of 2,354 GMTKN55 structures** (upstream's own binary too). `xtb --tblite` gave the right gradient on both chips with tblite 0.4 ([diagnosis](../../measurements/xtb-real/README.md#the-gradient-what-is-wrong-where)); this env's tblite 0.6.0 segfaults. A non-zero gradient norm here is expected — this is a fixed geometry, not a minimum. |
| `--etemp 300 --acc 1.0` | tighter `--acc` | passed explicitly so the recipe does not depend on defaults staying put; they happen to be xtb's current defaults. |

**Leave the fixture.** Three atoms is the point: the energy is a *published* number in xtb's own repository, which no larger molecule here would give you, and C2v water makes the symmetry identity below available for free. **Scale it** to real molecules once the check passes — GFN2 handles hundreds of atoms in seconds.

## Which box — [measured](../../measurements/xtb-real/README.md) (all of GMTKN55, 2,462 structures, 16 workers)

| chip | instance | tool wall | $/hr | compute $ | **billed $/result** |
|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 19 s | 0.6381 | 0.0034 | 0.0300 |
| Intel | `c8i.4xlarge` | 24 s | 0.7497 | 0.0050 | 0.0344 |
| **AMD** | `c8a.4xlarge` | **14 s** | 0.8622 | **0.0034** | **0.0251** |

**Energies are chip-independent** (1e-12 Eh across chips; 39 of 42 published GFN2 subset MAEs reproduced to the digit), so pick on price: AMD. The whole benchmark is shorter than boot, so 85–89% of the bill is not xtb.

## Shape, size, cost

One task on `c8g.large` (2 vCPU / 4 GiB), TTL 20m, cap $0.05. The SCF converges in 8 iterations in milliseconds; the recorded window is boot and image pull, so **these timings are not compute cost** ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: xtb's own committed energy, gradient norm and gap — all three reproduced on x86; on arm64 the gradient norm is 21× off and the recipe fails</summary>

### The checks

| observable | assertion | arm64 | x86_64 |
|---|---|---|---|
| SCF convergence | converged | 8 iterations | 8 iterations |
| **total energy** | **within 1.0e-7 of −5.070451355118** | **dev 2.810e-10** | **dev 2.810e-10** |
| **gradient norm** | **within 1.0e-7 of 0.006457420125** | **FAIL: 0.134752018160** | **dev 6.506e-09** |
| **HOMO-LUMO gap** | **within 1.0e-4 of 14.450372368833 eV** | **dev 6.706e-06** | **dev 6.706e-06** |
| **gradient: out-of-plane** | **gy = 0 for all atoms** | **3.220e-18** | **8.686e-17** |
| **gradient: C2 axis** | **gx(O) = 0** | **7.729e-18** | **9.154e-17** |
| **gradient: mirror** | **gx(H1) + gx(H2) = 0** | **0.000e+00** | **1.002e-16** |
| **gradient: mirror** | **gz(H1) − gz(H2) = 0** | **0.000e+00** | **9.975e-17** |

**All three reference values and their tolerances are xtb's own**, from `test/unit/test_gfn2.f90` at
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

It holds **whatever the reference values are**, so a version bump that moves the energies leaves
it intact. But it is not enough on its own, and this recipe is the proof: the arm64 gradient is
wrong by 21× and **still exactly C2v**. Symmetry catches a gradient with the wrong shape, not one
with the wrong size — which is why the gradient norm is asserted against upstream's value too.

### The gradient norm: x86 reproduces upstream's, arm64 does not, and the recipe asserts it

`test_gfn2.f90` pins `res%gnorm = 0.006457420125` at `thr = 1.0e-7`, the same tolerance as the
energy. The same pinned xtb 6.7.1 (`edcfbbe`) reports:

```text
                 total energy        HOMO-LUMO gap      gradient norm
arm64 (c8g)      -5.070451354837     14.450365663200    0.134752018160
x86_64 (c8i)     -5.070451354837     14.450365663200    0.006457413619
upstream test    -5.070451355118     14.450372368833    0.006457420125
```

Energy, gap, SCC iterations, charges and multipoles agree across chips to every printed digit; only
the gradient differs, ~21×, with the O `gz` changing sign (+8.28e-02 on arm64, −5.07e-03 on x86).
A gradient at a converged SCF is a fixed point, so this is not kernel round-off — and it is not
the BLAS: forcing `OPENBLAS_CORETYPE` to `NEOVERSEV2`, `NEOVERSEN1` and generic `ARMV8` gives a
bit-identical wrong gradient. The fault is in the aarch64 build, which conda-forge cross-compiles
from linux-64 and never runs xtb's unit tests on. Filed as
[conda-forge/xtb-feedstock#50](https://github.com/conda-forge/xtb-feedstock/issues/50) and
[aarchsci#46](https://github.com/playgroundlogic/aarchsci/issues/46). **Re-run arm64 when either
publishes a fixed build;** that run, and only that run, restores `last_verified`.

### x86_64

Passes on `c8i.large` (2026-10-17) through the env's linux-64 twin
(`envs-x86_64/comp-chem-823f1ef5e11e`, 203 of 207 versions identical, xtb 6.7.1, tblite 0.6.0 and
dftd4 4.2.0 among them; `cuda-version` 13.4 where arm64 pins 11.8, unused by xtb). Energy and gap
deviations are identical to arm64 (2.810e-10, 6.706e-06). The symmetry residuals are roundoff on
both: x86 `gy` 8.686e-17, `gx(O)` 9.154e-17, mirror sums 1.002e-16 / 9.975e-17.

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

Fails on non-convergence, the energy, gradient norm or gap outside xtb's own tolerance, or any
broken symmetry relation — but check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

Geometry optimisation, frequencies, GFN-FF, solvation (`--alpb`, `--gbsa`), the other GFN
parameterisations, and the dftd4 comparison above. xtb's broader test suite covers metals and
mindless molecules that this recipe does not touch.

</details>
