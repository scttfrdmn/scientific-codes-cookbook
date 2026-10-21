---
tool: psi4
tool_version: 1.11
env: dft
image: quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049
images_x86_64:
  - dft=docker.io/psi4/psi4@sha256:8c4c13f5e4e11b7506d2fd9f76751a7b45f41cb9fbb169ab520904d1d1caacb1
spawn_version: 0.139.1
last_verified: 2026-10-20
last_verified_x86_64: 2026-10-20
last_verified_amd: 2026-10-20
---
# Psi4 — Hartree-Fock on H₂, against the textbook energy

`psi4` computes the RHF/STO-3G energy of a hydrogen molecule — a Gaussian-basis quantum-chemistry SCF.

## Run it

```bash
make stage RECIPE=psi4    # the two scripts, h2.py and smoke.py; no data
spawn task run --spec "$(make -s spec RECIPE=psi4)" --wait
```

```python
import psi4
psi4.set_options({"scf_type": "pk"})   # exact integrals — Psi4 defaults to density fitting
psi4.geometry("H 0 0 0\nH 0 0 0.74")
e = psi4.energy("scf/sto-3g")     # → -1.116759 Ha; one task, geometry inline (h2.py)
```

## Which box — [measured](../../measurements/psi4-real/README.md) on the whole S66 set: SAPT0 and CP DF-MP2/aVTZ, 66 dimers

| chip | instance | tool wall | $/hr | **billed $/result** |
|---|---|---|---|---|
| Graviton4 | `r8g.4xlarge` | 7253 s | 0.9426 | 1.944 |
| Xeon 6975P-C | `r8i.4xlarge` | 4683 s | 1.1114 | 1.496 |
| **EPYC 9R45** | `r8a.4xlarge` | **3840 s** | 1.2781 | **1.421** |

**Take AMD: it's 1.89× faster than Graviton4 and 27% cheaper per set.** All three reproduce DESRES's published DESS66 SAPT0 and MP2 columns, all 924 numbers, to ≤ 1.24e-3 kcal/mol. Run 4 Psi4 processes × 4 threads with 28 GB each, not 16 × 1: smaller memory pushes the DF integrals to disk, which filled a 100 GB root and killed 24 jobs. On x86 this, and the recipe, use psi4's own `psi4/psi4:1.11.0` image (MKL), because the env twin does not import.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| H₂ at 0.74 Å (inline) | your own molecule + method | RHF/STO-3G on H₂ has a textbook value that doesn't depend on this run — that's what makes it a reference check, not a self-consistent band. |
| `scf/sto-3g` | a correlated method / larger basis | scale the theory freely, but memory becomes the constraint, not cores. SAPT0/aVTZ on a 34-atom dimer peaks at 18 GiB per process, and with less memory the DF integrals spill to disk ([measured](../../measurements/psi4-real/README.md)). The recipe pins the minimal case because its answer is externally known. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the reference identity is exact-or-wrong at any size, and H₂ makes it hand-checkable against every quantum-chemistry course. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 5m, cap $0.02. The SCF takes ~3 s. Recorded command window **95s** on Graviton4, **69s** on `c8i.large`, **59s** on `c8a.large` — boot, Docker install, and the ~0.8 GB image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the reference identity, the env note, pins, smoke check, run + verify</summary>

### The check — a reference identity

−1.1167 Hartree for H₂ at 0.74 Å in a minimal basis is a fixed, well-known literature number, so the band is tight (±0.0015). Psi4 gives −1.116759 through its own native integral and SCF code (an independent kernel from [gpaw](../gpaw/README.md)'s in the same env), run with `SCF_TYPE PK` for exact integrals — Psi4 *defaults* to density fitting, which gives −1.116783 (2.4e-5 higher), so PK makes the [pyscf](../pyscf/README.md) cross-check [like-with-like](../../practices/cross-checks.md). Because the reference is *external*, agreement means the numerics are right — not merely that Psi4 is internally consistent ([reproduce a number, don't self-check](../../practices/reference-from-tests.md)). Psi4 raises on non-convergence and the check confirms the converged-wavefunction line, so a silent failure can't pass. [pyscf](../pyscf/README.md) runs the same exact-integral calculation and cross-checks against this value, agreeing to 3e-7.

### Pins (data tier: none / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049` (tag `2026.09.04`, Psi4 1.11 — conda-forge `psi4 1.11 py314h2e14d3a_1`, cosign-signed, `linux/arm64`) |
| x86 image | `docker.io/psi4/psi4@sha256:8c4c13f5e4e11b7506d2fd9f76751a7b45f41cb9fbb169ab520904d1d1caacb1` (`1.11.0`, `linux/amd64`; `psi4 1.11 py312h42f99be_0`, einsums 1.1.3) — psi4's own upstream image |
| input | H₂ geometry, inline in `h2.py`; `h2.py` and `smoke.py` staged by `make stage`, sha256-checked in the task |

Psi4 lives in `dft` (not `comp-chem`) because of a python-version collision: it has no py313 arm64 build and `comp-chem`'s `xtb-python` has no py314 build, while `dft` already runs py314 (catalog issue #2). [siesta](../siesta/README.md) uses the same image under the same digest.

### x86_64 and AMD: psi4's own image, not the env twin

The `dft` env's linux-64 twin does not import psi4: `undefined symbol: _ZN7einsums13random_engineE`.
Its `psi4 1.11 py314hb58b17a_1` was linked against einsums 1.1.5's `random_engine` variable, and the
twin's solve picked einsums 1.1.6, where that symbol is a function (`…engineEv`). The arm64 build has
no einsums dependency. So x86 runs **psi4's own upstream image** at the same version (pinned above),
the one [psi4-real](../../measurements/psi4-real/README.md) ran the whole S66 set on. It differs from
the arm64 env in Python (3.12 vs 3.14) and BLAS (MKL alongside OpenBLAS 0.3.33).

Same spec, same assertions, 2026-10-20:

| chip | instance | SCF energy (Ha) |
|---|---|---|
| Graviton4 | `c8g.large` | −1.116759307378158 |
| Intel | `c8i.large` | −1.1167593073781577 (4.4e-16 from Graviton4) |
| AMD | `c8a.large` | −1.116759307378158 |

A converged SCF is a fixed point, so a last-bit difference is the expected size of any cross-chip
difference here. Psi4's side of the [pyscf](../pyscf/README.md) cross-check (3e-7) is the same
number on all three chips.

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

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). On x86 add `ARCH=x86_64` or `ARCH=amd` (outputs under `runs-x86_64/`, `runs-amd/`). Expect four objects (`psi4.out`, `psi4run.log`, `energy.json`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
