# PySCF — Hartree-Fock on H₂, cross-checked against Psi4

One task. `pyscf` computes the RHF/STO-3G energy of H₂, and the smoke check confirms it
agrees with the value `recipes/psi4` produced — two unrelated quantum-chemistry codebases
on the same molecule, method and basis.

> **What this recipe does and does not cover.** One SCF on H₂ in a minimal basis — enough
> to prove PySCF's native integral/SCF stack is correct on Graviton4 and agrees with a
> second code. Not a benchmark; no correlated method, large basis, or big molecule.

## Why PySCF, and the cross-code check

PySCF is a third independent SCF kernel in the `comp-chem`/`dft` catalog (after Psi4 and
NWChem), sharing no integral or SCF code with them. `recipes/psi4` (#20) fixed the same
calculation — H₂ at 0.74 Å, RHF/STO-3G — at **−1.116783 Ha** on Graviton4. This recipe runs
it in PySCF and checks the two agree: the RAxML-NG/IQ-TREE cross-validation move, in
quantum chemistry. Zero staging — the molecule is three lines of geometry in the task.

**The agreement is basis-limited, and that's the honest result.** PySCF gives −1.116759,
Psi4 −1.116783 — a difference of **2.4e-5 Ha** (~0.015 kcal/mol), not the ~1e-8 that
RAxML-NG and IQ-TREE reached. The reason is real and worth stating: STO-3G's contraction
coefficients are *not defined identically across packages*, so two correct HF codes land a
few times 1e-5 apart on a minimal basis — the **basis definition** is the limit, not the
SCF. Both are "correct HF/STO-3G" to the precision STO-3G is specified. So the cross-check
asserts agreement to **chemical accuracy** (< 1 mHa), which the 2.4e-5 comfortably meets;
it would catch a broken integral or SCF (those diverge by mHa–Ha), just not a 5th-decimal
basis nuance. Asserting 1e-8 here would be a check that fails for a reason unrelated to
correctness.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/comp-chem@sha256:a06f130ca3c8b514de1aa872536c9822c3ccb5322d594b935ae11627c5c80b09` |
| | tag `2026.09.04`, PySCF (+ rdkit, openmm, mdanalysis, …), cosign-signed, `linux/arm64` |
| input | H₂ geometry, **inline in the task** — nothing staged |

**Data tier: none / in-task.** Same `comp-chem` image as `recipes/vina` and
`recipes/rdkit`; this recipe invokes only PySCF.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| SCF converged | PySCF reports convergence | True |
| **PySCF energy** | −1.116759 ± 1e-4 Ha (RHF/STO-3G) | −1.116759 |
| **cross-code vs Psi4** | \|E − (−1.116783)\| < 1e-3 (agree to chemical accuracy) | 2.37e-5 |
| bound state | E < −1.0 Ha | −1.116759 |

The PySCF value is its own reproducible number; the cross-code line is the two-codebase
check, framed at the precision a minimal basis allows (see above).

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The SCF is
**~1 second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.62 GB** `comp-chem` image are the whole task. The recorded run's command window was
**71s** (01:13:18 → 01:14:29 UTC), energy −1.116759 and the Psi4 cross-check at 2.37e-5.
TTL was **retightened from that first real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02.
A loose TTL is a larger blast radius, not caution; the recorded run used the original 10m.
Disk is trivial.

## Running it

No `stage-inputs.sh` — the molecule is in the task.

```sh
spawn task run --spec recipes/pyscf/01-scf.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/pyscf/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect one
object (`smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.

**Note on parallel launches.** A transient AWS `Invalid IAM Instance Profile name` on a
parallel launch is the IAM-propagation race (spore-host/spawn#572), not a recipe fault —
re-run.
