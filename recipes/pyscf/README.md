---
tool: pyscf
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.104.0
last_verified: 2026-09-10
---
# PySCF — Hartree-Fock on H₂, cross-checked against Psi4

`pyscf` computes the RHF/STO-3G energy of H₂, cross-checked against [psi4](../psi4/README.md) — the same SCF from a second quantum-chemistry codebase.

> **What this covers.** One SCF on H₂ in a minimal basis — proof PySCF's native integral/SCF stack is correct on Graviton4 and agrees with a second code. Not a benchmark; no correlated method, large basis, or big molecule.

## Run it

```python
from pyscf import gto, scf
mol = gto.M(atom="H 0 0 0; H 0 0 0.74", basis="sto-3g")
scf.RHF(mol).kernel()     # → -1.116759 Ha
```

One task, one SCF. The molecule is three lines of inline geometry, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| H₂ at 0.74 Å (inline) | your own molecule + method | the minimal case is chosen because a second code ([psi4](../psi4/README.md)) fixes the same number to cross-check against. |
| the cross-check tolerance (< 1e-5 Ha) | keep it — it's method-justified | **load-bearing:** both codes run *exact* integrals (Psi4 with `SCF_TYPE PK`), so they agree to 3e-7 Ha. Psi4's density-fitting *default* would differ by 2.4e-5 — match the integral treatment or you measure the approximation, not the agreement ([match the modes](../../practices/cross-checks.md)). |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the cross-code identity holds at any molecule size, and H₂ makes it hand-checkable. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The SCF is ~1 s. Recorded command window **71s** — boot, Docker install, and the ~0.62 GB `comp-chem` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the cross-code check, pins, smoke check, run + verify</summary>

### The check — a matched-method cross-code identity

PySCF is a third independent SCF kernel in the catalog (after [psi4](../psi4/README.md) and [nwchem](../nwchem/README.md)), sharing no integral or SCF code. Both run H₂ at 0.74 Å, RHF/STO-3G, with **exact integrals** — Psi4 with `SCF_TYPE PK` — and agree to **3e-7 Ha**, near SCF-convergence precision. Matching the integral treatment is what makes that meaningful: Psi4 *defaults* to density fitting (DF), which gives −1.116783 Ha, **2.4e-5 above** the exact −1.116759; comparing that DF value against PySCF's exact one would measure Psi4's approximation, not the two codes' agreement — a method mismatch, not a basis limit ([match the modes](../../practices/cross-checks.md)). So the recipe sets PK and asserts **< 1e-5 Ha**: tight enough to be a real cross-validation (the [raxml-ng](../raxml-ng/README.md)/IQ-TREE move), loose enough to survive convergence noise. The full reasoning is on the [cross-checks page](../../practices/cross-checks.md).

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` (tag `2026.09.04`, PySCF + rdkit + openmm + mdanalysis + …, cosign-signed, `linux/arm64`) |
| input | H₂ geometry, inline — nothing staged |

Same `comp-chem` image as [vina](../vina/README.md) and [rdkit](../rdkit/README.md).

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| SCF converged | PySCF reports convergence | True | silent non-convergence |
| **PySCF energy** | −1.116759 ± 1e-4 Ha (RHF/STO-3G) | **−1.116759** | broken integral/SCF |
| **cross-code vs Psi4 (PK)** | \|E − (−1.116759)\| < 1e-5 (both exact integrals) | 3.1e-7 | either code wrong |
| bound state | E < −1.0 Ha | −1.116759 | garbage energetics |

### Run + verify

```sh
make run RECIPE=pyscf
make ls RECIPE=pyscf
```

a completed run does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect one object (`smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
