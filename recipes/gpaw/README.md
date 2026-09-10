---
tool: gpaw
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.104.0
---
# GPAW — plane-wave DFT on bulk silicon, serial and over 2 MPI ranks

Compute the LDA energy of bulk silicon in a plane-wave basis and reproduce aarch.science's published figure — proof GPAW computes correctly, and in real parallel, on Graviton4.

> **What this covers.** One small plane-wave SCF on a 2-atom Si cell (200 eV cutoff, 2×2×2 k-points), serial and 2-rank. Not a benchmark; no large cell, convergence study, or many-node scaling.

## Run it

```python
from ase.build import bulk
from gpaw import GPAW, PW
si = bulk("Si"); si.calc = GPAW(mode=PW(200), kpts=(2, 2, 2), xc="LDA")
si.get_potential_energy()          # -11.703689 eV — run serially and under `mpiexec -n 2`
```

One task, run twice (serial, then `mpiexec -n 2 gpaw python`). ASE builds the cell and the PAW datasets ship in the image, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 2-atom Si cell (ASE, in-code) | your own structure | the small cell is used because aarch.science published its energy to reproduce — that's the point, not a limit. |
| `PW(200)`, `kpts=(2,2,2)`, `LDA` | your cutoff / k-points / functional | standard GPAW knobs; scale them for your system. |
| serial + `mpiexec -n 2` | more ranks | the energy is rank-independent (below), so scale ranks for speed; [assert the rank count](../../practices/mpi-rank-count.md) so a serial build can't masquerade as parallel. |

Nothing is determinism scaffolding. **Leave the fixture:** it reproduces a published energy exactly and exercises the real PW/PAW kernel and the MPI path; a bigger cell is a longer run, not a more legible one (sizing is on the [sizing page](../../patterns/sizing.md)). Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the two vCPUs are for the two ranks), TTL 5m, cap $0.02. The two SCFs take ~4 s. Recorded command window 96s. **These timings are not compute cost** — boot and the 0.87 GB `dft` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)).

<details>
<summary>As shipped: the published reference, the rank guard, the scaling wall, pins, smoke check, run + verify</summary>

**A published reference plus a cross-validation.** This is the `dft` env's own D3 calculation, so the run reproduces aarch.science's figure exactly: **−11.703689 eV** ([reproduce a published number](../../practices/reference-from-tests.md)). The 2-rank leg must give the same energy (a cross-validation) **and** assert `gpaw.mpi.world.size == 2` — the [rank-count guard](../../practices/mpi-rank-count.md) aarch.science added after GPAW was one resolver tie from silently shipping a serial build that passes a naive "parallel == serial" check vacuously.

| observable | assertion | observed |
|---|---|---|
| **serial energy** | −11.703689 ± 1e-4 eV (D3 reference) | −11.703689 |
| **MPI world size** | exactly 2 | 2 |
| serial world size | exactly 1 | 1 |
| **serial == 2-rank** | \|serial − parallel\| < 1e-5 eV | identical |

**Scaling: MPI helps until it walls.** Swept on a 64-atom Si supercell across n = 1…48 ranks, wall time fell 618 → 22 s (~58% efficiency at 48) — then at n = 64 it *fails*, not slows: the cell can't decompose further ([sizing](../../patterns/sizing.md)'s "hits a wall" case). The energy is rank-independent (byte-identical −380.305 eV, drifting ~3e-5 eV at the largest counts from summation order), so scale ranks freely for speed; the only ceiling is a hard error, not a wasted bill.

**Pins** (data tier: bundled — the image digest is the only pin):

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9…` (tag `2026.09.04`, GPAW ≥25.7 `mpi_openmpi`, cosign-signed, `linux/arm64`) |
| input | bulk-Si cell (ASE) + PAW datasets (`gpaw-data`), in-code / bundled — nothing staged |

Same `dft` image as [nwchem](../nwchem/README.md); [siesta](../siesta/README.md) and [psi4](../psi4/README.md) pin the older `b356499…` — both immutable.

**Run + verify.**
```sh
make run RECIPE=gpaw
make ls RECIPE=gpaw
```
Smoke check runs inside the task; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-running: bump the `-r1` suffix.

</details>
