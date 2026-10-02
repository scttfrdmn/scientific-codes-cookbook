---
tool: psi4
tool_version: 1.12a4
env: dft
image: quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049
spawn_version: 0.104.0
last_verified: 2026-09-10
---
# Psi4 — Hartree-Fock on H₂, against the textbook energy

`psi4` computes the RHF/STO-3G energy of a hydrogen molecule — a Gaussian-basis quantum-chemistry SCF.

> **What this covers.** One SCF on H₂ in a minimal basis — proof Psi4's native integral/SCF stack is numerically correct on Graviton4 against a known reference. Not a benchmark; no correlated method, large basis, or big molecule.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=psi4)" --wait
```

```python
import psi4
psi4.set_options({"scf_type": "pk"})   # exact integrals — Psi4 defaults to density fitting
psi4.geometry("H 0 0 0\nH 0 0 0.74")
e = psi4.energy("scf/sto-3g")     # → -1.116759 Ha
```

One task, one `psi4.energy` call. The molecule (H₂ at 0.74 Å) is three lines of inline geometry, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| H₂ at 0.74 Å (inline) | your own molecule + method | RHF/STO-3G on H₂ has a textbook value that doesn't depend on this run — that's what makes it a reference check, not a self-consistent band. |
| `scf/sto-3g` | a correlated method / larger basis | scale the theory freely — though a correlated method or larger basis shifts the binding constraint to memory (integral storage grows steeply with basis size), which STO-3G on H₂ doesn't exercise; the recipe pins the minimal case because its answer is externally known. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the reference identity is exact-or-wrong at any size, and H₂ makes it hand-checkable against every quantum-chemistry course. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The SCF takes ~3 s. Recorded command window **85s** — boot, Docker install, and the 0.87 GB `dft` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

**Sizing:** no question at this scale (a minimal-basis SCF is sub-second on any box); at real scale the constraint shifts to memory with basis size — see *Make it yours*.

<details>
<summary>As shipped: the reference identity, the env note, pins, smoke check, run + verify</summary>

### The check — a reference identity

−1.1167 Hartree for H₂ at 0.74 Å in a minimal basis is a fixed, well-known literature number, so the band is tight (±0.0015). Psi4 gives −1.116759 through its own native integral and SCF code (an independent kernel from [gpaw](../gpaw/README.md)'s in the same env), run with `SCF_TYPE PK` for exact integrals — Psi4 *defaults* to density fitting, which gives −1.116783 (2.4e-5 higher), so PK makes the [pyscf](../pyscf/README.md) cross-check [like-with-like](../../practices/cross-checks.md). Because the reference is *external*, agreement means the numerics are right — not merely that Psi4 is internally consistent ([reproduce a number, don't self-check](../../practices/reference-from-tests.md)). Psi4 raises on non-convergence and the check confirms the converged-wavefunction line, so a silent failure can't pass. [pyscf](../pyscf/README.md) runs the same exact-integral calculation and cross-checks against this value, agreeing to 3e-7.

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049` (tag `2026.09.04`, Psi4 1.12a4, cosign-signed, `linux/arm64`) |
| input | H₂ geometry, inline — nothing staged |

Psi4 lives in `dft` (not `comp-chem`) because of a python-version collision: it has no py313 arm64 build and `comp-chem`'s `xtb-python` has no py314 build, while `dft` already runs py314 (catalog issue #2). [siesta](../siesta/README.md) uses the same image under the same digest.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| **SCF energy** | −1.1180 … −1.1150 Ha (textbook = −1.1167) | **−1.116759** | broken integral/basis/SCF |
| SCF converged | Psi4 reports a converged wavefunction | yes | silent non-convergence |
| energy is a float | the return value is a real number | float | garbage return |

### Run + verify

```sh
make run RECIPE=psi4
make ls RECIPE=psi4
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect three objects (`psi4.out`, `energy.json`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
