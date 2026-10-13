---
tool: cantera
tool_version: "3.2.0"
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d
gap_x86_64: the aarch.science `comp-chem` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-13) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-11
---
# Cantera — thermochemistry held to exact identities and detailed balance

Equilibrates methane-air on Graviton4 and checks what must be true: atoms conserved, four thermodynamic identities at machine precision, and forward rates equal to reverse at equilibrium. For anyone doing combustion or kinetics on ARM.

## Run it

```bash
make stage RECIPE=cantera     # once: the checks only — gri30 ships inside cantera
spawn task run --spec "$(make -s spec RECIPE=cantera)" --wait
make ls RECIPE=cantera

gas = ct.Solution("gri30.yaml")              # 53 species, 325 reactions — published
gas.TPX = 1800.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
gas.equilibrate("TP")
gas.forward_rates_of_progress - gas.reverse_rates_of_progress   # ~0: detailed balance
gas.cp_mole - gas.cv_mole                    # == R exactly, for an ideal gas
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the off-equilibrium control | an **unburned** mixture | **that doesn't work.** CH₄/O₂/N₂ with no radicals is *kinetically frozen* — every GRI-Mech rate is exactly 0, so the control reports 0 and the detailed-balance result proves nothing. The control holds the equilibrium composition at a **shifted temperature** instead. |
| `gri30.yaml` | your mechanism | it ships **inside** the package, so it is version-matched to cantera and needs no staging. 33 mechanism files are installed; `ct.Solution("…")` finds them by name. |
| `equilibrate("TP")` | `"HP"`, `"UV"` | `HP` holds enthalpy fixed and gives the adiabatic flame temperature — **2225.52 K** here, against the ~2225 K textbook value for this mixture. |
| asserting `cp − cv = R` | a measured band | `R = N_A·k_B` is **exact by SI definition**, so staging derives it rather than quoting it. This is a constant, not a fit. |
| equilibrium only | a reactor network or 1-D flame | this page checks thermo and equilibrium. Transport, ignition delay and flame speeds are untouched — see below. |

**Leave the fixture.** Nothing is fetched: stoichiometric methane-air (`CH4:1, O2:2, N2:7.52`, the `2 × 3.76` air ratio staging verifies) with a mechanism from the installed package. **Scale it** to your own mechanism — every identity here is independent of the chemistry.

## Shape, size, cost

One task on `m8g.large` (2 vCPU / 8 GiB), TTL 30m as a **backstop** with `cost_limit` $0.12 as the real guard. All of it — three state points, four equilibrium solves, 325 reaction rates — is **6 s** of a 1m32s window; 38 s is installing Docker and 47 s pulling the image ([layout](../../patterns/layout-and-effective-cost.md)).

<details>
<summary>As shipped: four identities at 4e-16, detailed balance at 1e-14 with a control that separates it by 14 orders of magnitude, and a published flame temperature</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged bytes | match pin | 1 of 1 |
| cantera / numpy / python | read from the running install | **3.2.0** / 2.5.4 / 3.13.16 |
| mechanism in the package | must be present | `gri30.yaml` ✓ |
| **species / reactions** | **== 53 / 325** (published) | **53 / 325** |
| **`h = u + pv`** | **< 1e-14 relative** | **1.339e-16** |
| **`g = h − Ts`** | **< 1e-14** | **2.421e-16** |
| **`cp − cv = R`** | **< 1e-14** | **4.375e-16** |
| **`p = ρRT/M`** | **< 1e-14** | **2.298e-16** |
| `ΣY = 1`, `ΣX = 1` | < 1e-14 | 1.110e-16, 0 |
| **element conservation** | **< 1e-9 through equilibration** | **1.830e-11** |
| control: composition moved | **> 0.01**, or conservation is vacuous | **0.190066** |
| **detailed balance** | **< 1e-12 of the largest rate** | **1.352e-14** |
| irreversible net rate | < 1e-15 | 5.681e-18 |
| **control: off equilibrium** | **net/forward > 0.1** | **1.781** |
| idempotence of `equilibrate` | < 1e-9 | 1.204e-12 |
| enthalpy drift through `HP` | < 1e-8 | 1.790e-10 |
| **adiabatic flame temperature** | **2200–2250 K** (published ~2225) | **2225.52 K** |
| determinism | two solves byte-identical | yes |

### Four identities, at the float64 floor

Each relates properties cantera computes by a separate route, which is what makes them
constraints rather than restatements — the same shape as arviz's `mcse == sd/√ess`:

```text
h - (u + pv)        1.339e-16      first law, molar
g - (h - Ts)        2.421e-16      Gibbs definition
(cp - cv) - R       4.375e-16      ideal gas; cp-cv = 8314.462618 = R exactly
p - rho R T / M     2.298e-16      equation of state
```

Checked at 300 K / 1 atm pure O₂, 1200 K / 5 atm rich CH₄/O₂, and 2500 K / 0.3 atm lean
H₂/O₂/N₂ — so they are not an artifact of one convenient state. The 1e-14 bound is ~45
float64 epsilons: tight enough to be a real constraint, loose enough to survive summation
order. **`R` is derived in staging from `N_A · k_B`**, both exact by SI definition since 2019,
so `cp − cv = R` is checked against a definition rather than a quoted number.

### Conservation, and the controls that stop it being vacuous

**Element conservation** through an equilibrium solve is the kind of identity this catalog
prefers: whatever the chemistry does, atoms are conserved, and no reference value is needed.

```text
elemental mole fractions, max drift     1.830e-11
composition change through the solve    0.190066      <- CONTROL
```

That second line is load-bearing. If equilibration had been a no-op, "elements are conserved"
would be true and empty — so the run asserts the composition genuinely moved. 0.19 is CH₄ and O₂
becoming CO₂, H₂O, CO and radicals.

**Detailed balance** is the stronger claim: at equilibrium, every reversible reaction's forward
and reverse rates of progress must be equal. Across 325 reactions (309 reversible):

```text
max |forward - reverse| / max|forward|      1.352e-14      at equilibrium
max |net| among irreversible reactions      5.681e-18
net / forward                               1.781          OFF equilibrium  <- CONTROL
```

**The off-equilibrium control took two attempts, and the first failure is the interesting part.**
The obvious choice — an *unburned* CH₄/O₂/N₂ mixture — reports exactly **zero**: the mixture
contains no radicals, essentially every GRI-Mech reaction needs one, so every rate is identically
0. "Unburned" is **kinetically frozen**, not "far from equilibrium with active chemistry". The
control caught that and failed the run, which is precisely its job: without it, `fwd == rev` would
have been satisfied just as well by a rate routine returning zeros everywhere.

The working control holds the **equilibrium** composition (radicals present, rates non-zero) at a
shifted temperature, where the rate constants no longer match the composition. Net/forward is then
**1.781** against **1.4e-14** at equilibrium — a separation of 14 orders of magnitude, so detailed
balance is demonstrably a property of the converged state.

### A published number that costs nothing

```text
adiabatic flame temperature, stoichiometric CH4/air at 1 atm:   2225.52 K
textbook value for this mixture and mechanism:                  ~2225 K
enthalpy drift through the HP solve:                             1.790e-10
```

The 2200–2250 K window is loose enough to survive solver noise and tight enough that a corrupted
thermo database could not pass. The enthalpy drift is a second identity in the same solve: `HP`
equilibration holds enthalpy fixed *by construction*, so any drift is solver error, and 1.8e-10 is
the measured fidelity.

### Pins

| | |
|---|---|
| data | **none staged.** `gri30.yaml` ships in `<cantera>/data/` (33 mechanism files installed); references are SI constants, thermodynamic identities and a published flame temperature |
| checks | `identities.py`, pinned by sha256 |
| image | `quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374…` — cantera 3.2.0, rdkit 2026.03.1, pyscf 2.14.0, openmm 8.6.1, ase 3.29.0, numpy 2.5.4, python 3.13.16 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest, so verify the tag and pin the arm64 digest.

cantera was added to this env on [aarchsci#38](https://github.com/playgroundlogic/aarchsci/issues/38),
which measured the cost: 207 → 223 packages with **python and rdkit unchanged**. This env resolves
python 3.13.16 for an unrelated reason (psi4's old py313 ceiling, which is why psi4 lives in
`dft`), and cantera has aarch64 builds for py310–py314, so it fitted the env's python rather than
moving it. The six older comp-chem recipes still pin the pre-cantera digest, which is correct —
repinning them is a change to their input and would need a re-run, not a hash edit.

### Run + verify

```sh
make stage RECIPE=cantera
spawn task run --spec "$(make -s spec RECIPE=cantera)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/cantera/r1/score.tsv" -
```

Fails on a pin mismatch, a mechanism that is not 53 species and 325 reactions, any of the four
identities above 1e-14, elemental drift above 1e-9, a composition that did not change (so
conservation would be vacuous), forward and reverse rates differing by more than 1e-12 at
equilibrium, an irreversible reaction carrying net rate, a control state with zero forward rates or
net/forward below 0.1, a non-idempotent solve, enthalpy drifting through an `HP` equilibration, a
flame temperature outside 2200–2250 K, or a non-deterministic repeat — but check the bucket
regardless ([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**Time-dependent chemistry.** No reactor networks, no ignition-delay calculation, no `IdealGasReactor`
integration — equilibrium and instantaneous rates only, so nothing here exercises the ODE
integrator that most kinetics work depends on.

**Flames.** `FreeFlame`, burner-stabilised and counterflow solvers are all untouched, and they are
the headline capability this env gained. **Transport properties** — viscosity, conductivity and
diffusion coefficients are computed by a separate model that is never evaluated. **Surface
chemistry** (`Interface`), despite `SiF4_NH3_mec.yaml` and the ammonia-Ru mechanisms shipping in the
package. **Mechanism variety**: only GRI-Mech 3.0, so nothing says how the identities behave on a
mechanism with different thermo parameterisations. **Cross-validation against a second
thermochemistry code**, which this env cannot offer — pyscf and xtb compute electronic structure,
not reaction rates.

</details>
