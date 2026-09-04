# NWChem — RHF/STO-3G on water, serial and over 2 MPI ranks

One task. `nwchem` computes the Hartree-Fock energy of a water molecule serially and
again over two MPI ranks, and the smoke check confirms the energy reproduces
aarch.science's published verification and that the two runs agree.

> **What this recipe does and does not cover.** It runs one small SCF on H₂O serially
> and on 2 ranks — enough to prove NWChem 7.3.1 and its OpenMPI build compute correctly
> and in parallel on Graviton4. NWChem is one of the few QC codes that genuinely scales
> multi-node; this is not that demo (it's single-node, 2-rank), and not a benchmark.

## Why one task, and why nothing is staged

NWChem is one tool, run twice in the same task (serial + 2-rank) to make the comparison
the recipe is built around. The H₂O geometry and the RHF/STO-3G directives are an inline
input deck written by heredoc, and the STO-3G basis ships in the env, so there is **no
input to stage** and no `stage-inputs.sh`; the image digest is the only pin.

## The check: a published reference plus an internal cross-validation

RHF/STO-3G on H₂O at its experimental geometry is a small, completely determined number.
aarch.science ran exactly this when it verified NWChem into the `dft` env
(`dft.smoke.py`), so the recipe reproduces their published figure:

| | Total SCF energy, H₂O RHF/STO-3G |
|---|---|
| **this run (Graviton4)** | **−74.963023 Ha** |
| aarch.science `dft` D3 reference | −74.963023 Ha |

That is the RELION move (reproduce a published result). And the recipe runs the same
calculation **over 2 MPI ranks** and requires the energy to match the serial one — an
internal cross-validation, like `recipes/lammps` (serial vs 2-rank), which catches an
MPI stack that links but computes wrong or hangs. NWChem computes through an
integral/SCF stack that shares no code with the `dft` env's gpaw or psi4, so it's also a
third independent SCF kernel in the same image.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/dft@sha256:0740fab9721da533ce153cae3590b1c6822dd0decfa1838b0753e76ba4434a4e` |
| | tag `2026.09.04` / content-hash `s5cb0d94e928d`, NWChem 7.3.1 (`py314_mpi_ts` — OpenMPI), cosign-signed, `linux/arm64` |
| input | H₂O geometry + STO-3G, **inline / bundled** — nothing staged |

**Data tier: bundled / in-task.** Geometry is in the command; the STO-3G basis ships in
the env. The image digest is the only pin.

**Note the digest.** This is a *newer* `dft` than `recipes/siesta` and `recipes/psi4`
pin (`b356499…`): aarch.science added NWChem to `dft` and republished under the same
`2026.09.04` date-tag, so the tag moved to a new digest `0740fab9…` (content-hash
`s5cb0d94e928d`). The older recipes keep their old digest — immutable and still valid;
this one pins the NWChem-containing build. NWChem is the `_ts` (two-sided) OpenMPI
variant, not `_pr`: `_pr` has no serial mode (so it can't satisfy the serial-vs-parallel
check) and needs more `/dev/shm` than a container's default 64 MB.

`dft` now **requires the container entrypoint** — NWChem has the feedstock build path
compiled in as its basis-set default and relies on `activate.d` to set
`NWCHEM_BASIS_LIBRARY`; skip activation and it exits 255. spawn runs the container
through its entrypoint, so this is satisfied, and the task also asserts the variable is
set before running.

## Smoke check

Measured in this image, on this input, before any launch.

| observable | assertion | observed |
|---|---|---|
| **serial SCF energy** | **−74.963023 ± 1e-5 Ha** (aarch.science `dft` D3 reference) | **−74.963023128766** |
| MPI ranks (2-rank leg) | exactly 2 (NWChem's own `nproc`) | 2 |
| serial ranks | exactly 1 | 1 |
| **serial == 2-rank** | **\|serial − parallel\| < 1e-6** (identical SCF energy) | **identical** |

The serial energy is the reference reproduction; the serial-vs-2-rank equality is the
cross-validation. Here the two agreed to all printed digits — RHF is deterministic and
NWChem's parallelisation is over the integral evaluation, not a reduction that reorders
the SCF. The 2-rank leg reading `nproc = 2` from NWChem's own banner is what proves the
OpenMPI build actually parallelised rather than running two independent serial jobs.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The two SCF runs
take **~4 seconds** together; the 2 vCPUs are for the two MPI ranks, not throughput, and
H₂O/STO-3G needs almost no memory.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**~0.87 GB** `dft` image are the whole task; the science is seconds. The recorded run's
command window was **93s** (23:08:25 → 23:09:58 UTC), and the SCF energy came back
−74.963023128766 — matching aarch.science's reference. TTL was **retightened from that
first real run**: 10m → **5m** (~2.3× the ~2-minute instance life), `cost_limit` $0.03 →
$0.02. A loose TTL is a larger blast radius, not caution; the recorded run used the
original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — geometry and basis are in the image.

```sh
spawn task run --spec recipes/nwchem/01-scf.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/nwchem/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke
check runs *inside* the task, and the bucket listing is the second half of it. Expect
three objects (`nwchem-serial.out`, `nwchem-2rank.out`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both. NWChem overwrites its
own database and output, so there is no checkpoint guard to defeat.

**Note on parallel launches.** If launched alongside other tasks and it dies with an AWS
`Invalid IAM Instance Profile name` error, that is a transient IAM-propagation race
(spore-host/spawn#572), not a recipe fault — no instance was created, so just re-run it.
