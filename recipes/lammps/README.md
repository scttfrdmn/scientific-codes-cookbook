# LAMMPS — the Lennard-Jones melt, serial and over 2 MPI ranks

One task. `lmp_mpi` runs the canonical Lennard-Jones melt twice — once serially, once
over two MPI ranks — and the smoke check confirms the two independent computations land
on the same total energy. That agreement, not the energy itself, is the point.

> **What this recipe does and does not cover.** It runs a *tiny* MD: 256 Lennard-Jones
> atoms for 50 steps, on an analytic potential that needs no data file. It proves
> LAMMPS 2025.07.22 runs correctly on Graviton4 and that its **MPI build actually
> parallelises** — it is not a benchmark, and 2 ranks on one small box does not
> exercise real domain decomposition at scale, long-range solvers, or multi-node runs.

## Why one task, and why this input

LAMMPS is one tool and this is one invocation of it, run twice in the same task to make
the comparison the recipe is built around. There is no reusable intermediate to split.

**The input is analytic, so nothing is staged.** The LJ melt uses `pair_style lj/cut`,
a closed-form potential — there is genuinely no data file to fetch. The conda package
ships only the binaries and the python module (not the `bench/` tree), so the input
script is the content of LAMMPS's own `bench/in.lj`, embedded in the task by heredoc.
It builds a 4×4×4 fcc lattice (256 atoms), seeds velocities from a fixed seed, and
integrates 50 steps of NVE. This is exactly aarch.science's `md.smoke.py` melt, so the
result is comparable to what they published for this image.

> A note on the name. The worklist called this "LAMMPS (bundled in.lj)". `bench/in.lj`
> is not *in* the conda package — but its contents are a fixed, well-known input, so
> embedding them inline is zero-staging in the same spirit, and the recipe says so
> rather than implying a file was shipped.

## Why run it twice

Because the strongest check available here is a **cross-validation the tool performs
against itself.** Domain decomposition splits the atoms across ranks and changes the
order in which pairwise forces are summed, so the serial and 2-rank runs are two
genuinely independent computations of the same trajectory. If they agree to
floating-point-reordering tolerance, both the serial kernels and the MPI communication
are correct; if the MPI build were silently broken or mis-summing across ranks, they
would diverge. This is stronger than any single run's energy band, and it is the same
class of check as `recipes/raxml-ng` (two codes, one answer) — here it is one code,
two decompositions, one answer.

The MPI build is the entire reason the `md` env pins `lammps=*=cpu_*mpi_openmpi*`, so an
unexercised MPI path would be exactly where a packaging gap hides. Running the 2-rank
case makes it impossible to ship a serial-only build unnoticed.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` |
| | tag `2026.09.04`, LAMMPS 2025.07.22 (`cpu_*mpi_openmpi*` build), cosign-signed, index has one `linux/arm64` manifest |
| input | LJ melt (`bench/in.lj` content), **embedded in the task** — nothing staged |

**Data tier: bundled/analytic.** The potential is closed-form and the input script is
in the spec, so the image digest is the only pin — no `stage-inputs.sh`, no S3 input.

Same image as `recipes/gromacs` — the `md` env carries both engines. This recipe
invokes only `lmp_mpi` and `mpiexec`.

## Smoke check

Measured in this image, on this input, before any launch. The serial-vs-2-rank equality
is a tolerance on floating-point reordering, not on physics; everything else is exact or
a wide physical band.

| observable | assertion | observed |
|---|---|---|
| atoms created | exactly 256 (4×4×4 fcc, 4-atom basis) | 256 |
| MPI ranks | exactly 2 (the parallel run really used 2) | 2 |
| final total energy | −4 … −1 reduced units (physical) | −2.2990327 |
| **serial == 2-rank** | **\|serial − parallel\| < 1e-2** (independent decompositions agree) | **exact (0)** |
| NVE conserved | total-energy drift < 0.05 over 50 steps | 0.0081 |
| LAMMPS completed | `Total wall time` in both logs | yes |

Three of these earn their place:

**`serial == 2-rank` is the headline**, and here the two runs agreed *bit-for-bit* (the
melt is small enough that the reduction order happened to match), comfortably inside the
`1e-2` tolerance the check allows for the general case. Assert the tolerance, not the
zero: a future LAMMPS or MPI build could reorder legitimately and still be correct.

**NVE conservation is a physical law**, not a fitted band — with no thermostat the total
energy must be conserved, and a drift of 0.008 in reduced units over 50 steps says the
integrator is numerically sound. A broken force kernel shows up as drift long before it
shows up as a wrong absolute energy.

**`Total wall time` is a completion sentinel** LAMMPS writes only on a clean finish, so a
run killed mid-trajectory (whose thermo table would still look plausible to a parser)
cannot pass.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. Both runs together
take **~3 seconds**; the 2 vCPUs exist for the two MPI ranks, not for throughput, and
`c8g.large` is the smallest box that gives an honest 2-rank run. Memory is irrelevant at
256 atoms.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**1.19 GB** `md` image are the whole task; the science is 3 seconds inside it. The
recorded run's command window was **106s** (19:22:38 → 19:24:24 UTC), and serial and
2-rank agreed on `-2.2990327` — bit-identical to the local run. TTL was **retightened
from that first real run**: 10m originally, now **5m** (~2.3× the ~2.2-minute instance
life), with `cost_limit` following it down $0.03 → $0.02. A loose TTL is a larger blast
radius, not caution; the recorded run used the original 10m. Disk is trivial.

## Running it

No `stage-inputs.sh` — the input is in the spec.

```sh
spawn task run --spec recipes/lammps/01-melt.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/lammps/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the
smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect three objects (`s.log`, `p.log`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous records. Bump the
`-r1` suffix in both `task_id` and the output prefix to keep both. LAMMPS overwrites its
own logs, so there is no checkpoint guard to defeat.
