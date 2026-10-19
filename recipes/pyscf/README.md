---
tool: pyscf
env: comp-chem
image: quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d
env_x86_64: comp-chem-1243f25ebfec
spawn_version: 0.130.1
last_verified: 2026-10-17
last_verified_x86_64: 2026-10-17
last_verified_amd: 2026-10-18
---
# PySCF — Hartree-Fock on H₂, cross-checked against Psi4

`pyscf` computes the RHF/STO-3G energy of H₂, cross-checked against [psi4](../psi4/README.md) — the same SCF from a second quantum-chemistry codebase.

> **What this covers.** One SCF on H₂ in a minimal basis — proof PySCF's native integral/SCF stack is correct on Graviton4 and agrees with a second code. Not a benchmark; no correlated method, large basis, or big molecule.

## Run it

```bash
spawn task run --spec "$(make -s spec RECIPE=pyscf)" --wait
```

```python
from pyscf import gto, scf
mol = gto.M(atom="H 0 0 0; H 0 0 0.74", basis="sto-3g")
scf.RHF(mol).kernel()     # → -1.116759 Ha
```

One task, one SCF. The molecule is three lines of inline geometry, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| H₂ at 0.74 Å (inline) | your own molecule + method | the minimal case is chosen because a second code ([psi4](../psi4/README.md)) fixes the same number to cross-check against; scaling to a real molecule or basis shifts the constraint to memory (integral storage grows steeply with basis size), which H₂ doesn't exercise. |
| the cross-check tolerance (< 1e-5 Ha) | keep it — it's method-justified | **load-bearing:** both codes run *exact* integrals (Psi4 with `SCF_TYPE PK`), agreeing to 3e-7 Ha; Psi4's density-fitting default would differ by 2.4e-5 — [match the modes](../../practices/cross-checks.md). |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the cross-code identity holds at any molecule size, and H₂ makes it hand-checkable. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The SCF is ~1 s. Recorded command window **88s** — boot, Docker install, and the ~0.62 GB `comp-chem` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

**Sizing:** no question at this scale (a minimal-basis SCF is sub-second on any box); real use shifts the constraint to memory with basis size — see *Make it yours*.

<details>
<summary>As shipped: the cross-code check, pins, smoke check, run + verify</summary>

### The check — a matched-method cross-code identity

PySCF is a third independent SCF kernel in the catalog (after [psi4](../psi4/README.md) and [nwchem](../nwchem/README.md)), sharing no integral or SCF code. Both run H₂ at 0.74 Å, RHF/STO-3G, with **exact integrals** (Psi4 with `SCF_TYPE PK`) and agree to **3e-7 Ha** — a real cross-validation (the [raxml-ng](../raxml-ng/README.md)/IQ-TREE move). Psi4's density-fitting *default* would instead differ by 2.4e-5 (−1.116783 vs the exact −1.116759); [why matching the integral treatment matters](../../practices/cross-checks.md) is the like-with-like discipline, not a basis limit. The asserted **< 1e-5 Ha** is set to survive SCF-convergence noise.

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:b5c1b1ec10cfbb5374d0e733310244f84b59dc4ce99620b645c4efb6f49c439d` (tag `2026.10.11`, PySCF 2.14.0 + rdkit + openmm + mdanalysis + …, cosign-signed, `linux/arm64`) |
| input | H₂ geometry, inline — nothing staged |

Same `comp-chem` image as [vina](../vina/README.md) and [rdkit](../rdkit/README.md).

The previous pin, `comp-chem@sha256:a06f130ca3c8…` (lock-hash `c8cd4a3c4c30`), can no longer be
re-solved: conda-forge later capped its mdtraj 1.11.1 at `numpy <2.5`, and it shipped numpy 2.5.2
(found 2026-10-17). It still runs, and its arm64 lock still installs without a solve
([why](../../practices/environment-durability.md)); this recipe moved to the 2026.10.11 env.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed (Graviton4) | x86_64 (`c8i`) | catches |
|---|---|---|---|---|
| SCF converged | PySCF reports convergence | True | True | silent non-convergence |
| **PySCF energy** | −1.116759 ± 1e-4 Ha (RHF/STO-3G) | **−1.116759** | identical | broken integral/SCF |
| **cross-code vs Psi4 (PK)** | \|E − (−1.116759)\| < 1e-5 (both exact integrals) | 3.07e-7 | identical | either code wrong |
| bound state | E < −1.0 Ha | −1.116759 | identical | garbage energetics |

**x86_64** runs the same spec on the env's linux-64 twin (`envs-x86_64/comp-chem-1243f25ebfec`;
three versions differ from arm64, none of them PySCF's or its BLAS). The energy is an SCF **fixed
point**, and it is identical across chips at every printed digit.

### Run + verify

```sh
make run RECIPE=pyscf
make ls RECIPE=pyscf
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect one object (`smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
