---
tool: gromacs
tool_version: 2026.3
env: md
image: quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9
spawn_version: 0.104.0
---

# GROMACS — molecular dynamics of 216 SPC waters, reproducing a published energy

One task. `gmx_mpi grompp` builds a run input from a 216-molecule SPC water box and
`gmx_mpi mdrun` integrates it for 20 steps, and the smoke check confirms the potential
energy lands on the number aarch.science published for this exact image.

> **What this recipe does and does not cover.** It runs a *small, short* MD: 216 water
> molecules for 40 fs, on data GROMACS itself ships. That is enough to prove GROMACS
> 2026.3 runs correctly on Graviton4 and reproduces a known energy — it is **not** a
> performance benchmark and does not exercise PME, a real membrane/protein system, or
> multi-node scaling. The GROMACS binary here is a NEON build (`SIMD instructions:
> ARM_NEON_ASIMD`), so it does not use SVE/SVE2 on Graviton; that is a conda-forge
> packaging choice, noted so no one reads a timing here as GROMACS's ceiling on ARM.

## Why one task, and why this input

GROMACS is one tool and this is one invocation of it — `grompp` then `mdrun` are two
subcommands of the same binary in one pass, not two stageable tasks. There is no
expensive reusable intermediate to split out.

**The input is bundled, so nothing is staged.** `spc216.gro` (a 216-molecule SPC water
box, "MAR. 1984") and the `amber99sb-ildn.ff` force field both ship *inside* the
gromacs conda package at `/opt/conda/share/gromacs/top`. The topology and `.mdp` are
six lines of configuration each, generated in the task by heredoc — deliberately the
**exact** setup in aarch.science's own `md.smoke.py`, because that makes the result
directly comparable to the figure they published when they built this image.

> A note on the name. The Round One worklist called this recipe "GROMACS (benchMEM)".
> benchMEM is a downloadable membrane benchmark; it is **not** in the image, and it
> carries no bundled reference energy to check against. For the *correctness* check the
> shipped 216-SPC box is better on both counts: zero staging, and a *physical* quantity
> to assert — water's cohesive energy — rather than a timing of an arbitrary system.
> benchMEM found its proper use in the **[sizing sweep](../../patterns/sizing.md)**, where
> "how fast, how many cores, how much money" is the question and no reference energy is
> needed — the two jobs, correctness and scaling, want different inputs.

So this is not "GROMACS produced plausible numbers." It is a **reproduction**: the same
setup, on the same bytes, that aarch.science ran when verifying the `md` env, checked
value-for-value on Graviton4. The initial coordinates carry no velocities and none are
generated, so the run is deterministic — and its potential energy is **bit-stable
across thread counts** (measured: identical to 2 dp at `-ntomp` 1, 2 and 4), so the
check needs no thread caveat.

## Pins

| | |
|---|---|
| image | `quay.io/aarchsci/md@sha256:1ee941664add6f83b367c012d0cc670ffc837e83ee491125993de72e88c22ab9` |
| | tag `2026.09.04`, GROMACS 2026.3-conda_forge (mixed precision, MPI, ARM_NEON_ASIMD), cosign-signed, index has one `linux/arm64` manifest |
| input | `spc216.gro` + `amber99sb-ildn.ff`, **bundled in the image** — nothing staged |

**Data tier: bundled in the image.** The input ships inside the pinned gromacs package,
so the image digest *is* the input pin — there is no `stage-inputs.sh` and no S3 input.

The image is one of aarch.science's **curated envs** (`md`: gromacs + lammps +
ambertools), not a per-tool image. This recipe invokes only `gmx_mpi`; `recipes/lammps`
runs the other engine in the same image, under the same digest.

## Smoke check

Measured in this image, on this input, before any launch. The potential-energy check is
the one doing the real work, and it is a reproduction of a published value, not a band
around a hunch.

| observable | assertion | observed |
|---|---|---|
| atoms in system | exactly 648 (216 SPC × 3), from `out.gro` | 648 |
| **potential energy** | **−9629 … −9626 kJ/mol** (published D3: −9627.9) | **−9627.87** |
| per-water energy | −46 … −43 kJ/mol/water (physical cohesion) | −44.573 |
| total energy readable | `gmx energy` returns a value (exercises the `.edr` reader) | −8911.69 |
| mdrun completed | the `Performance:` summary is present | yes |

Two of these earn their place for reasons worth naming.

**The potential energy is a cross-check against a published result**, the same move
`recipes/relion` makes against the RODA archive. aarch.science's `md.smoke.py` reported
`-9627.9 kJ/mol` (`-44.6`/water) for 216 SPC waters when it verified this image on its
build host; Graviton4 gives `-9627.87`. Two runs of the same deterministic code on
different hosts agreeing to five significant figures is a much stronger statement than
"it ran" — it says the C++/SIMD kernels are numerically correct on Graviton, not merely
that they load. The band is `±1.5 kJ/mol` only because the published figure was rounded
to one decimal; the run itself is bit-stable.

**Per-water energy is a physics floor.** SPC water cohering at roughly −44 kJ/mol per
molecule is a real, checkable property; a mis-built topology or a wrong force field
lands nowhere near −44, so this catches a garbage system independently of the exact
number.

`grompp`/`mdrun` completion is confirmed by GROMACS's `Performance:` summary, which it
writes only on a clean finish — a run killed part-way leaves logs but no summary.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The MD itself is
**sub-second** — 20 steps of 648 atoms — so memory and cores are irrelevant to
correctness here; `c8g.large` is the smallest compute-family box and there is no reason
to pay for more. `-ntomp 2` matches its 2 vCPUs.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
**1.19 GB** `md` image are the whole task; the science takes under a second — the image
pull is the bulk of it. The recorded run's command window was **103s** (19:22:38 →
19:24:21 UTC), and the potential energy came back `-9627.87` — bit-identical to the
local run and matching aarch.science's published D3 figure. Read any wall time here as
"the platform started a box", never as a GROMACS benchmark.

TTL was **retightened from that first real run**: 10m originally (a boot-and-pull margin
before any Graviton measurement), now **5m** — about 2.3× the ~2.2-minute instance life
— with `cost_limit` following it down $0.03 → $0.02. A loose TTL is a larger blast
radius, not free caution; the recorded run used the original 10m. Disk is trivial:
~1.2 GB image, a few hundred KB of output.

## Sizing, MPI, and going bigger

The 216-SPC run proves *correctness*; it says nothing about *how many cores to give a real
job*. That was measured separately on a real membrane system (benchMEM, 81,743 atoms) across
8→192 cores — the curves and the method live on the **[sizing page](../../patterns/sizing.md)**.
Three facts from it change how you launch GROMACS, and they belong here because they bite every
run:

- **[Assert the rank count from inside the run](../../practices/mpi-rank-count.md).** conda-forge ships `nompi` builds at *higher*
  build numbers than the openmpi ones, so an unpinned solve can hand back a serial binary that,
  under `mpirun -n 2`, runs two independent rank-0 calculations — same energy, false parallelism.
  Read the count GROMACS reports (`Using N MPI process(es)`) and assert it is what you launched.
- **The launch dominates the decomposition.** `mpirun -np 1 -ntomp 8` pins 8 threads to *one*
  core — **1.9 vs 13.5 ns/day, a 7× loss** — because `mpirun` binds one rank to one core by
  default. Give each rank its cores (`--map-by slot:PE=<threads> --bind-to core`) before you
  tune anything else. This dwarfs the ~25% you'd chase optimizing ranks-vs-threads.
- **Bigger is faster but not cheaper.** benchMEM scales to 192 cores (11× throughput) while
  cost-per-result more than doubles, the efficiency bending across 96→192 — where the run begins
  spanning two NUMA nodes (measured; Graviton4 c8g is single-socket, so it's a node crossing
  *within* the socket, not between sockets) and atoms-per-rank halves. Size to the cost knee
  unless the deadline is now.

The *result* never depends on core count here — a single-point energy on identical coordinates
is decomposition-invariant, so only speed and cost move. That is the opposite of an assembler,
where thread count changes the answer ([flye](../flye/README.md)), and it means you can trust a
GROMACS energy from any rank layout while sizing purely for cost.

## Running it

No `stage-inputs.sh` — the input is in the image.

```sh
spawn task run --spec recipes/gromacs/01-md.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/gromacs/r1/
```

`--wait` exiting 0 does **not** prove the outputs exist: a task whose declared output
fails to stage is still recorded `completed` / `exit_code: 0` (spore-host/spawn#561).
The smoke check runs *inside* the task, where it can fail the task; the bucket listing
is the second half of the same check. Expect five objects.

**Re-running.** `task_id` is fixed, so a re-run overwrites the previous
`completion.json` and `command.log`. Bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records. The task writes into `/tmp` and overwrites its own
outputs, so there is no checkpoint guard to defeat.
