---
tool: nwchem
tool_version: 7.3.1
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.104.0
---
# NWChem — RHF/STO-3G on water, serial and over 2 MPI ranks

`nwchem` computes the Hartree-Fock energy of a water molecule, serially and again over two MPI ranks — a third quantum-chemistry SCF engine in the `dft` env.

> **What this covers.** One small SCF on H₂O, serial and 2-rank — proof NWChem 7.3.1 and its OpenMPI build compute correctly and in parallel on Graviton4. NWChem is one of the few QC codes that genuinely scales multi-node; this is not that demo (single-node, 2-rank) and not a benchmark.

## Run it

```bash
nwchem h2o.nw                     # serial RHF/STO-3G
mpirun -n 2 nwchem h2o.nw         # 2 ranks — same SCF energy
```

One task, run twice. The H₂O geometry and RHF/STO-3G directives are an inline input deck; the STO-3G basis ships in the env, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| H₂O at experimental geometry (inline `.nw`) | your own molecule + method | RHF/STO-3G on H₂O is a completely-determined reference number; scale the theory freely. |
| `mpirun -n 2` | more ranks / multi-node | NWChem's parallelism is over integral evaluation (not a reducing SCF), so the energy is rank-independent — scale for speed. |

RHF is deterministic — **nothing is determinism scaffolding**. **Leave the fixture:** the reference energy is exact-or-wrong at any basis, and a bigger molecule is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the two vCPUs are for the two ranks), TTL 5m, cap $0.02. The two SCF runs take ~4 s. Recorded command window **93s** — boot, Docker install, and the ~0.87 GB `dft` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the reference reproduction, the rank-count guard, the digest note, pins, smoke check, run + verify</summary>

### The check — a published reference plus an internal cross-validation

aarch.science ran exactly this when it verified NWChem into the `dft` env (`dft.smoke.py`), reporting **−74.963023 Ha**; Graviton4 gives −74.963023128766 — the [reproduce-a-published-result move](../../practices/reference-from-tests.md). The 2-rank leg must match the serial one (catches an MPI stack that links but computes wrong), and NWChem's integral/SCF stack shares no code with the env's [gpaw](../gpaw/README.md) or [psi4](../psi4/README.md), so it's a third independent SCF kernel in the same image.

**[Assert the rank count from inside the run](../../practices/mpi-rank-count.md).** The 2-rank leg reads `nproc = 2` from NWChem's own banner — proof the OpenMPI build parallelised rather than running two serial jobs.

### Pins (data tier: bundled / in-task)

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` (tag `2026.09.04` / `s5cb0d94e928d`, NWChem 7.3.1 `py314_mpi_ts` OpenMPI, cosign-signed, `linux/arm64`) |
| input | H₂O geometry + STO-3G, inline / bundled — nothing staged |

**Note the digest.** This is a *newer* `dft` than [siesta](../siesta/README.md) and [psi4](../psi4/README.md) pin (`b356499…`): aarch.science added NWChem and republished under the same `2026.09.04` date-tag, moving it to `0740fab9…`. The older recipes keep their old digest — immutable and still valid. NWChem is the `_ts` (two-sided) variant, not `_pr` (which has no serial mode and needs more `/dev/shm` than a container's default). `dft` now requires the container entrypoint (NWChem relies on `activate.d` to set `NWCHEM_BASIS_LIBRARY`, or it exits 255); spawn runs through the entrypoint, and the task asserts the variable is set.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| **serial SCF energy** | −74.963023 ± 1e-5 Ha (`dft` D3 reference) | **−74.963023128766** | broken integral/SCF |
| MPI ranks (2-rank leg) | exactly 2 (NWChem's `nproc`) | 2 | serial build under `mpirun` |
| serial ranks | exactly 1 | 1 | wrong launch |
| **serial == 2-rank** | \|serial − parallel\| < 1e-6 | **identical** | MPI computes wrong |

### Run + verify

```sh
make run RECIPE=nwchem
make ls RECIPE=nwchem
```

The smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Expect three objects (`nwchem-serial.out`, `nwchem-2rank.out`, `smoke-check.txt`). Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
