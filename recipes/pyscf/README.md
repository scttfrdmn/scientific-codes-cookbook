---
tool: pyscf
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09
spawn_version: 0.104.0
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
| the cross-check tolerance (< 1 mHa) | keep it — it's basis-justified | **load-bearing:** PySCF and Psi4 agree only to 2.4e-5 Ha because STO-3G's contraction coefficients aren't standardized across packages; asserting 1e-8 would fail for a reason unrelated to correctness ([justify the tolerance by the problem, not the noise](../../practices/cross-checks.md)). |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the cross-code identity is basis-limited at any size, and H₂ makes it hand-checkable. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The SCF is ~1 s. Recorded command window **71s** — boot, Docker install, and the ~0.62 GB `comp-chem` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the cross-code check, pins, smoke check, run + verify</summary>

### The check — a basis-limited cross-code identity

PySCF is a third independent SCF kernel in the catalog (after [psi4](../psi4/README.md) and [nwchem](../nwchem/README.md)), sharing no integral or SCF code. Psi4 fixed H₂ at 0.74 Å, RHF/STO-3G, at **−1.116783 Ha**; PySCF gives −1.116759 — a difference of **2.4e-5 Ha** (~0.015 kcal/mol), not the ~1e-8 that [raxml-ng](../raxml-ng/README.md) and IQ-TREE reached. The reason is real: STO-3G's contraction coefficients are not defined identically across packages, so two correct HF codes land a few times 1e-5 apart on a minimal basis — the *basis definition* is the limit, not the SCF. So the check asserts agreement to **chemical accuracy** (< 1 mHa), which catches a broken integral or SCF (those diverge by mHa–Ha) but not a 5th-decimal basis nuance. The full reasoning is on the [cross-checks page](../../practices/cross-checks.md).

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
| **cross-code vs Psi4** | \|E − (−1.116783)\| < 1e-3 (chemical accuracy) | 2.37e-5 | either code wrong |
| bound state | E < −1.0 Ha | −1.116759 | garbage energetics |

### Run + verify

```sh
make run RECIPE=pyscf
make ls RECIPE=pyscf
```

a completed run does **not** prove the outputs exist — an exit code says the command ran, never that its output is real; the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect one object (`smoke-check.txt`). Re-run: bump the `-r1` suffix.

</details>
