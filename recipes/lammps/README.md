---
tool: lammps
tool_version: 2025.07.22
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.104.0
---
# LAMMPS — the Lennard-Jones melt, serial and over 2 MPI ranks

`lmp_mpi` runs the canonical Lennard-Jones melt twice — once serial, once over two MPI ranks.

> **What this covers.** A tiny MD: 256 LJ atoms for 50 steps on an analytic potential. Proof LAMMPS 2025.07.22 runs correctly on Graviton4 and its MPI build **actually parallelises**. Not a benchmark; 2 ranks on one small box is not domain decomposition at scale, long-range solvers, or multi-node.

## Run it

```bash
lmp_mpi -in in.lj                    # serial
mpiexec -n 2 lmp_mpi -in in.lj       # 2 ranks — same trajectory, independently decomposed
```

One task, run twice. `in.lj` is LAMMPS's own `bench/in.lj` (a 4×4×4 fcc lattice, 256 atoms, fixed velocity seed, 50 NVE steps), embedded inline — the conda package ships binaries but not the `bench/` tree, and the LJ potential is closed-form, so nothing is staged.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `in.lj` — 256-atom LJ melt | your own input script + data file | the melt needs no data file (analytic `lj/cut`); a real system stages a `read_data` file through S3. |
| `velocity ... 87287` (fixed seed) | your run's seed | fixed so the serial and 2-rank runs are the *same* trajectory to compare; change it freely for production. |
| `mpiexec -n 2` | scale ranks to your box | the *result* is decomposition-invariant (below), so scale ranks purely for speed → [sizing](../../patterns/sizing.md). |

**Leave the fixture:** the serial-vs-2-rank identity is exact-or-tolerance for a correct MPI build at any size, and 256 atoms make it fast and hand-checkable. A bigger melt is a longer run, not a more legible one. Leave-it.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB — the two vCPUs exist for the two ranks, not throughput), TTL 5m, cap $0.02. Both runs together take ~3 s. Recorded command window **106s** — boot, Docker install, and the 1.19 GB `md` image pull are the whole task ([why](../../practices/what-this-does-not-cover.md)). **These timings are not compute cost.**

<details>
<summary>As shipped: the serial-vs-2-rank cross-validation, the rank-count guard, pins, smoke check, run + verify</summary>

### The check — one code, two decompositions, one answer

The serial and 2-rank runs are two genuinely independent computations of the same trajectory; agreement to floating-point-reordering tolerance means both the kernels and the MPI communication are correct. This is the same class as [raxml-ng](../raxml-ng/README.md)'s two-codes-one-answer, here as one-code-two-decompositions.

**[Assert the rank count from inside the run](../../practices/mpi-rank-count.md).** The `md` env pins `lammps=*=cpu_*mpi_openmpi*` and the check reads LAMMPS's own `with 2 MPI task(s)` and asserts 2 — so a silently-serial build (which would print the same energy) can't pass.

### Pins (data tier: bundled / analytic)

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` (tag `2026.09.04`, LAMMPS 2025.07.22 `cpu_*mpi_openmpi*`, cosign-signed, `linux/arm64`) |
| input | LJ melt (`bench/in.lj` content), embedded in the task — nothing staged |

Same image as [gromacs](../gromacs/README.md) — the `md` env carries both engines.

### Smoke check (inside the task; measured before launch)

| observable | assertion | observed | catches |
|---|---|---|---|
| atoms created | exactly 256 (4×4×4 fcc) | 256 | wrong lattice |
| MPI ranks | exactly 2 | 2 | serial build under `mpiexec` |
| final total energy | −4 … −1 reduced units | −2.2990327 | garbage physics |
| **serial == 2-rank** | \|serial − parallel\| < 1e-2 | **exact (0)** | broken MPI reduction |
| NVE conserved | drift < 0.05 over 50 steps | 0.0081 | broken integrator |
| LAMMPS completed | `Total wall time` in both logs | yes | run killed mid-trajectory |

`serial == 2-rank` agreed bit-for-bit here; the check asserts the `1e-2` *tolerance*, not the zero, because a future build could reorder legitimately and still be correct.

### Run + verify

```sh
spawn task run --spec recipes/lammps/01-melt.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/lammps/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist (spore-host/spawn#561): the smoke check runs *inside* the task, and the bucket listing is the second half of it. Expect three objects (`s.log`, `p.log`, `smoke-check.txt`). Re-run: bump the `-r1` suffix.

</details>
