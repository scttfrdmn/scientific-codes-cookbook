---
tool: siesta
tool_version: 5.4.2
env: dft
image: quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049
spawn_version: 0.104.0
---
# SIESTA — bulk-silicon DFT, reproducing SIESTA's own committed reference

`siesta` runs a self-consistent DFT calculation on bulk silicon over two MPI ranks — LCAO-pseudopotential DFT, the SIESTA method.

> **What this covers.** One SCF on a 2-atom Si cell (single-ζ-polarised basis, 3×3×3 k-grid) — proof SIESTA 5.4.2 runs a real, converged, MPI-parallel DFT calculation on Graviton4 and lands on the published energy. Not a benchmark; no large cell, geometry relaxation, or many-node scaling.

## Run it

```bash
mpirun -n 2 siesta < si.fdf     # bulk-Si DFT, 2 ranks → siesta: Total = -214.377236 eV
```

One task. The `.fdf` input is a few lines of config generated inline; only the pseudopotential is data, and only it is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `Si.psf` from SIESTA's test suite at tag **5.4.2** | your element's pseudopotential | **load-bearing:** conda-forge `siesta` ships *no* pseudopotentials, so one must be staged; the version match to the container is what makes the run reproduce a committed reference (a psf from another version is a different number). |
| the 2-atom Si cell + 3×3×3 k-grid (inline `.fdf`) | your own system | the small cell reproduces a *published* number — that's the point, not a limit. |
| `mpirun -n 2` | more ranks | the energy is rank-independent (serial == 2-rank, measured); scale for speed. |

Deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** reproducing SIESTA's own committed energy is the strongest check available, and it's exact at this cell size. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — sized for the 2 ranks), TTL 5m, cap $0.02. The SCF takes ~2 s on 2 ranks. Recorded command window **93s** — boot, Docker install, and the 0.87 GB `dft` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the pseudopotential sourcing, the reference reproduction, the rank guard, pins, smoke check, run + verify</summary>

### The pseudopotential problem, and the reference it manufactures

conda-forge `siesta` ships no pseudopotentials, so the recipe stages `Tests/Pseudos/Si.psf` from `siesta-project/siesta` at tag `5.4.2` — the version-matched [reproduction](../../practices/reference-from-tests.md) move: pseudopotential, input, and `Reference/psf.out` all from the same version, so the run matches its `-214.377236 eV`. Staging a pinned file is allowed where build-time constraints forbid bundling; the digest is verified on the box.

**[Assert the rank count](../../practices/mpi-rank-count.md).** The `dft` env pins `siesta=*=mpi_openmpi*` and the check reads `Running on 2 nodes` — proof the MPI path ran, not a silently-serial build.

### Pins (data tier: stable public source with a durable id)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:b356499318a2a257b475cbd2d35372d0e91c2fba2b35e9b5c2051596814bc049` (tag `2026.09.04`, SIESTA 5.4.2 aarch64 MPI, cosign-signed, `linux/arm64`) |
| pseudopotential | `Tests/Pseudos/Si.psf` from `siesta-project/siesta` tag **`5.4.2`** — `sha256:0afddde3…` (152,736 B) |

`stage-inputs.sh` fetches, verifies and uploads it once. [psi4](../psi4/README.md) uses the same image under the same digest.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| pseudopotential sha256 | matches the pin | OK | wrong/corrupt psf |
| SCF converged | `SCF cycle converged after N iterations` | yes (4 iters) | hit iteration limit / died mid-cycle |
| MPI ranks | `Running on 2 nodes` | 2 | serial build under `mpirun` |
| **total energy** | −214.377236 ± 0.01 eV (5.4.2 committed reference) | **−214.377236** | broken numerics |

The energy reproduces `Tests/01.PseudoPotentials/Reference/psf.out`'s `-214.377236 eV` to six decimals, identical between serial and 2-rank; the ±0.01 band is a cross-host floating-point margin, far tighter than SIESTA's own test tolerance.

### Run + verify

```sh
recipes/siesta/stage-inputs.sh          # once; fetch + verify + upload Si.psf (~150 KB)
spawn task run --spec recipes/siesta/01-scf.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/siesta/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect two objects (`psf.out`, `smoke-check.txt`). Re-run: bump the `-r1` suffix. A transient `Invalid IAM Instance Profile name` on a parallel launch is the IAM-propagation race (spore-host/spawn#572) — re-run.

</details>
