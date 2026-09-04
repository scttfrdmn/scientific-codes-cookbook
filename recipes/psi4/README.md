# Psi4 — Hartree-Fock on H₂, against the textbook energy

One task. `psi4` computes the RHF/STO-3G energy of a hydrogen molecule, and the smoke
check confirms it lands on the textbook value.

> **What this recipe does and does not cover.** It runs one SCF on H₂ in a minimal
> basis — enough to prove Psi4's native integral/SCF stack is numerically correct on
> Graviton4 against a known reference. It is not a benchmark and does not exercise a
> correlated method, a large basis, or a big molecule.

## Why one task, and why nothing is staged

Psi4 is one tool and this is one `psi4.energy` call. The molecule — H₂ at 0.74 Å — is
three lines of geometry written inline in the task, so there is **no input to stage**
and no `stage-inputs.sh`; the image digest is the only pin.

The result is checkable against a number that does not depend on this run at all:
RHF/STO-3G for H₂ at 0.74 Å is a **textbook value, −1.1167 Hartree**, reproduced in
every quantum-chemistry course. So this is a reference identity, not a self-consistent
band — Psi4 computes it through its own native integral and SCF code (an independent
kernel from gpaw's in the same `dft` env), and the check is whether that code agrees
with the literature.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049` |
| | tag `2026.09.04`, Psi4 1.12a4, cosign-signed, index has one `linux/arm64` manifest |
| input | H₂ geometry, **inline in the task** — nothing staged |

**Data tier: none / in-task.** The molecule is generated in the command; the image
digest is the only pin.

The image is aarch.science's curated `dft` env; this recipe invokes only `psi4`.
`recipes/siesta` uses the same image under the same digest. Psi4 lives in `dft` rather
than `comp-chem` because of a python-version collision (it has no py313 arm64 build and
`comp-chem`'s `xtb-python` has no py314 build); `dft` already runs py314, so Psi4 joins
there. See the catalog note on issue #2.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| **SCF energy** | **−1.1180 … −1.1150 Ha** (textbook RHF/STO-3G H₂ = −1.1167) | **−1.116783** |
| SCF converged | Psi4's output reports a converged wavefunction | yes |
| energy is a float | the return value is a real number | float |

**The SCF energy is a reference identity.** −1.1167 Hartree for H₂ at 0.74 Å in a
minimal basis is a fixed, well-known literature number, so the band is tight (±0.0015,
essentially "matches the textbook to three decimals"). Psi4 gives −1.116783. A wrong
integral evaluation, a mis-specified basis, or a broken SCF lands nowhere near it, and
because the reference is external, agreement means the numerics are right — not merely
that Psi4 is internally consistent. Psi4 raises on non-convergence, and the check also
confirms the converged-wavefunction line, so a silent failure cannot pass.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The SCF takes
**~3 seconds**; H₂/STO-3G needs a fraction of the memory and the box is the smallest
compute-family option.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**0.87 GB** `dft` image are the whole task; the science is seconds. The recorded run's
command window was **85s** (20:06:46 → 20:08:11 UTC), and the SCF energy came back
`-1.116783 Ha` — matching the textbook value. TTL was **retightened from that first real
run**: 10m → **5m** (~2.3× the ~2-minute instance life), `cost_limit` $0.03 → $0.02. A
loose TTL is a larger blast radius, not caution; the recorded run used the original 10m.
Disk is trivial.

## Running it

No `stage-inputs.sh` — the molecule is in the task.

```sh
spawn task run --spec recipes/psi4/01-scf.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/psi4/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the
smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect three objects (`psi4.out`, `energy.json`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump
the `-r1` suffix in both `task_id` and the output prefix to keep both. Psi4 overwrites
its own output, so there is no checkpoint guard to defeat.
