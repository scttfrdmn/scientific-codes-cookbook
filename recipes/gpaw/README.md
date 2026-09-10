---
tool: gpaw
env: dft
image: quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e
spawn_version: 0.104.0
---

# GPAW — plane-wave DFT on bulk silicon, serial and over 2 MPI ranks

One task. `gpaw` computes the LDA energy of bulk silicon in a plane-wave basis, serially
and again over two MPI ranks, and the smoke check confirms the energy reproduces
aarch.science's published verification and that the parallel run really parallelised.

> **What this recipe does and does not cover.** It runs one small plane-wave SCF on a
> 2-atom Si cell (200 eV cutoff, 2×2×2 k-points), serial and 2-rank — enough to prove
> GPAW and its OpenMPI build compute correctly and in parallel on Graviton4. Not a
> benchmark; no large cell, convergence study, or many-node scaling.

## Why GPAW, and why one task

GPAW is the **primary** DFT engine of the `dft` env — the cookbook already has the three
*secondary* engines from that env (SIESTA, Psi4, NWChem) but not the flagship, so this
fills the obvious gap. It's a plane-wave/PAW code, a different method from SIESTA's LCAO
pseudopotentials or the Gaussian-basis QC codes, so it's a genuinely distinct kernel.
One `python3` invocation, run twice (serial + 2-rank) for the comparison.

Nothing is staged: ASE builds the Si diamond cell in code, and GPAW's PAW datasets ship
in the image (`gpaw-data`), so there is **no input to stage** and no `stage-inputs.sh`.

## The check: a published reference plus an internal cross-validation

This is the `dft` env's own D3 reference calculation, so the recipe reproduces the figure
aarch.science published for this image:

| | bulk-Si PW/LDA total energy (2×2×2 k-pts, 200 eV) |
|---|---|
| **this run (Graviton4)** | **−11.703689 eV** |
| aarch.science `dft` D3 reference | −11.703689 eV |

That's the reproduction move. And the same calculation over **2 MPI ranks** must give the
same energy — an internal cross-validation. The parallel leg additionally asserts
`gpaw.mpi.world.size == 2`: this is the guard aarch.science added after finding GPAW was
one resolver tie from silently shipping a *serial* build, which under `mpiexec -n 2` runs
two independent rank-0 calculations that both print the same energy and pass a naive
"parallel matches serial" check vacuously. Requiring `world.size > 1` closes that hole, so
this recipe carries the same guard.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` |
| | tag `2026.09.04` / `s5cb0d94e928d`, GPAW ≥25.7 (`mpi_openmpi`), cosign-signed, `linux/arm64` |
| input | bulk-Si cell (ASE) + PAW datasets (`gpaw-data`), **in-code / bundled** — nothing staged |

**Data tier: bundled / in-task.** The image digest is the only pin. This is the same
`dft` image `recipes/nwchem` pins (`0740fab9…`, the NWChem-containing republish); SIESTA
and Psi4 pin the older `b356499…` — both immutable and valid.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| **serial energy** | −11.703689 ± 1e-4 eV (dft D3 reference) | −11.703689 |
| **MPI world size** | exactly 2 (real MPI, not two serial jobs) | 2 |
| serial world size | exactly 1 | 1 |
| **serial == 2-rank** | \|serial − parallel\| < 1e-5 eV | identical |

The serial energy is the reference reproduction; `world.size == 2` proves the parallel
leg actually parallelised; serial-vs-2-rank agreement is the cross-validation. Here the
two agreed to all printed digits.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The two SCFs take
**~4 seconds** together; the 2 vCPUs are for the two MPI ranks.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.87 GB** `dft` image are the whole task; the science is seconds. The recorded run's
command window was **96s** (23:34:19 → 23:35:55 UTC), and the energy came back
−11.703689 eV — matching the D3 reference. TTL was **retightened from that first real
run**: 10m → **5m**, `cost_limit` $0.03 → $0.02. A loose TTL is a larger blast radius,
not caution; the recorded run used the original 10m. Disk is trivial.

## Scaling: MPI helps, until it walls

GPAW is the [sizing page](../../patterns/sizing.md)'s "hits a wall" case, and it's the clean
counterpart to GROMACS. Swept on a 64-atom Si supercell across n = 1/2/4/8/16/32/48 MPI ranks, wall
time fell 618 → 22 s — **MPI genuinely speeds DFT up** (k-point/band parallelism), scaling to ~48
ranks at ~58% efficiency. Then at n = 64 it does not slow down — it **fails**: the cell can't be
decomposed any further. "It got slower" and "it stopped working" are different problems, and GPAW is
the second kind.

Crucially, **the energy is rank-independent** — byte-identical −380.305 eV from 1 rank to 48, drifting
only ~3×10⁻⁵ eV at the largest counts (floating-point summation order). So unlike flye, the *result*
never moves with core count; scale ranks freely for speed and assert the energy exactly. The only
caution is the ceiling: past the parallelization wall you get an error, not a wasted bill. (The
`world.size` assertion in the smoke check is the same discipline — it proves the run is *actually*
parallel, not a serial binary launched under `mpirun`.)

## Running it

No `stage-inputs.sh` — the cell and PAW data are in the image.

```sh
spawn task run --spec recipes/gpaw/01-si.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/gpaw/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect
three objects (`gpaw-1.txt`, `gpaw-2.txt`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both.

**Note on parallel launches.** If launched alongside other tasks and it dies with an AWS
`Invalid IAM Instance Profile name` error, that is a transient IAM-propagation race
(spore-host/spawn#572), not a recipe fault — no instance was created, so just re-run it.
