---
tool: siesta
tool_version: 5.4.2
env: dft
image: quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049
spawn_version: 0.111.4
last_verified: 2026-09-30
---
# SIESTA — bulk Si equilibrium lattice constant, cross-validated against GPAW

Scans the Si equation of state to get a₀ = 5.4042 Å, and reproduces SIESTA's own committed reference energy in the same run. For anyone doing localised-basis DFT on ARM.

> **GPAW gets 5.4139 Å on the same problem** — two codes, two basis treatments, **0.0097 Å apart**. That agreement is the check; a total energy from an LCAO code and a plane-wave code cannot be compared at all.

## Run it

```bash
make stage RECIPE=siesta   # once: Si.psf from the SIESTA 5.4.2 tag
make run   RECIPE=siesta   # 8 SCFs (1 reference + 7 scan points), ~33 s
make ls    RECIPE=siesta   # smoke-check.txt + siesta-scan.dat

mpiexec -n 2 siesta < si.fdf   # DZP basis, 6×6×6 k-grid, 200 Ry mesh, LDA
```

## Which box — measured (same inputs, same digest, 2 MPI ranks on a 4-vCPU box)

| generation | instance | 8 SCFs | **$/run** | a₀ |
|---|---|---|---|---|
| Graviton2 | `c6g.xlarge` | 52 s | 0.00196 | 5.4042 |
| Graviton3 | `c7g.xlarge` | 36 s | **0.00145** | 5.4042 |
| Graviton4 | `c8g.xlarge` | 33 s | 0.00146 | 5.4042 |
| **Graviton5** | `c9g.xlarge` | **28 s** | **0.00135** | 5.4042 |

Graviton2→5 is **1.86× faster** — the *smallest* gain of the four physics codes here (GROMACS 2.43×, GPAW 2.33×, LAMMPS 2.24×), which is what small dense linear algebra looks like next to plane-wave or particle work. And as in [GPAW](../gpaw/README.md), **Graviton3 and Graviton4 are cost-tied** ($0.00145 vs $0.00146) — two DFT codes now show that step not paying for itself.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| bulk Si + `Si.psf` | your system + its pseudopotential | the psf is the hard part: conda-forge ships none, so this one comes from SIESTA's own version-matched test suite. |
| `PAO.BasisSize DZP` | `SZP` (faster) or `TZP` (better) | the basis *is* the accuracy knob in SIESTA, and it moves a₀ — the cross-check below only holds at DZP or better. |
| 7-point scan | denser, or a geometry relaxation | the scan is what turns a total energy into an observable someone else can check. |

**Leave the workload** — an equation of state is the smallest thing yielding a comparable observable, and it runs in 33 s. **Scale it** to larger cells or a finer basis; both move a₀, so re-converge first.

<details>
<summary>As shipped: two independent checks, why only a₀ is comparable, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| reference total energy | **exactly −214.377236 eV** (SIESTA's committed 5.4.2 value) | **−214.377236** |
| MPI ranks | == launched (`Running on N nodes`) | **2** |
| SCF converged | `SCF cycle converged` present, every scan point | **yes, 8/8** |
| a₀ | 5.35–5.46 Å (LDA literature band for Si) | **5.4042 Å** |
| **a₀ vs GPAW** | **< 0.03 Å** | **0.0097 Å** |

Two genuinely independent checks in one run. The first is a **published-number reproduction**: the
reference point uses SIESTA's own `Tests/01.PseudoPotentials` case at the matching 5.4.2 tag — SZP
basis, 3×3×3 k-grid, 150 Ry — so the total energy must land on the value the project committed. That
is only possible because the pseudopotential is version-matched; a psf from another release is a
different number ([why staging from a code's own test suite pays](../../practices/cross-checks.md)).

The second is the **cross-code agreement** on a₀, at the production DZP basis. The reference point
and the scan deliberately use different settings, because the committed reference exists only at the
SZP settings while a₀ needs a basis good enough to be comparable.

### Why only a₀ is comparable, and what sets the tolerance

The two scans look nothing alike:

```text
a (Å)    SIESTA (eV)     GPAW (eV)
5.37     -215.630950     -11.882121
5.43     -215.629867     -11.885148
```

An LCAO pseudopotential total energy and a PAW plane-wave total energy have **different zeros** —
they differ by ~204 eV here, which is not an error, it is a different reference for the core
electrons. So comparing them would be meaningless; only a *structural* observable survives.

Matching the modes is what makes even that fair: both runs use **LDA** (SIESTA's default XC, so GPAW
was set to match rather than to PBE), the same **6×6×6** k-mesh, and the **same quadratic fit** over
the same seven lattice constants. The remaining difference is the basis treatment, which is what the
tolerance budgets: **0.03 Å** is the expected DZP-versus-converged-plane-wave difference for a
covalent semiconductor, with the k-mesh contributing under 0.005 Å at 6×6×6. The observed 0.0097 Å
clears it with 3× margin — tight enough to be a real cross-validation of the numerics, loose enough
not to fail on a basis detail. Full comparison:
[measurements/dft-crosscheck](../../measurements/dft-crosscheck/README.md).

a₀ came out **identical to four decimals on all four generations**, as it must: DFT converges to a
fixed point, so the chip cannot move the answer.

### Pins

| | data tier |
|---|---|
| SIESTA | `quay.io/aarchsci/dft@sha256:b3564993…` (5.4.2, `linux/arm64`) |
| pseudopotential | `siesta-project/siesta` @ `5.4.2` → `Tests/Pseudos/Si.psf`, sha256 `0afddde32f30…` |
| structure | 2-atom diamond cell written inline in the spec |

The tag is load-bearing twice over: it matches the packaged SIESTA version, and it is what makes the
committed reference energy the right number to expect.

### Run + verify

```sh
make run RECIPE=siesta
make ls  RECIPE=siesta
```

Expect `smoke-check.txt` with `ref_energy_eV -214.377236`, `ref_ranks 2` and `a0_Ang 5.4042`.

</details>
