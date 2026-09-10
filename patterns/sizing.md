# How many cores should I ask for? — sizing and the scaling knee

> **Throughput keeps climbing while your money doesn't.** On one node, GROMACS on 82k atoms went from 13 ns/day at 8 cores to 148 at 192 — an 11× speedup — but the **cost per result more than doubled**. Faster is real. Cheaper it is not.

The instinct from a shared cluster is to grab the biggest box: the queue was expensive and you got one shot, so you asked for everything. Drop it. Here a box costs what it costs by the second, and the question is not "how many cores can I get" but "how many cores does *this run* actually use before they stop paying for themselves."

That point — where adding cores stops helping enough to justify the cost — is the **knee**. It is not a fixed number. It moves with your problem, and for some codes it isn't a slowdown at all, it's a wall. This page is how to find yours, measured on the real codes in this cookbook.

## The one quantity that decides it: work per core

A parallel run scales until each core runs out of work to do without talking to the others. For MD that's **atoms per rank**; for a DFT run it's **k-points × bands per rank**; for an assembler it's the length of the serial phases no thread can help with. Past that point, cores spend their time communicating instead of computing, and the curve bends.

So the knee is set by *your problem's size divided by the cores*, not by the cores alone. The same 96-core box is wasteful for a small job, well-matched for a medium one, and barely enough for a large one. **Measure your run; don't inherit someone else's core count.**

**First, though — which resource are you actually sizing on?** This page is for **compute-bound** runs, where cores are the dial and the knee is where they stop paying. If your job spends its time waiting on *bytes* rather than computing on them — streaming a reference, reading an index — you're **data-movement-bound**, and the dials are NIC bandwidth (cold reads) and RAM (the cache), not cores. Size that on [Copy, mount, or share?](data-movement.md) instead. Everything below assumes compute is the bottleneck.

## Three codes, three shapes — know which yours is

The interesting part is that "more cores" does three categorically different things depending on the code. Every recipe here is one of these three:

- **The answer moves** — [flye](../recipes/flye/README.md). Thread count changes the *assembly*: 10 / 12 / 11 / 14 contigs at `-t 1/2/4/8` on the same reads. Not "more fragmented" — it wanders, and you can't predict the direction. Speed is sublinear too (3.2× at 4 threads, only 4.8× at 8). So more threads is both less useful *and* less reproducible — which is why the recipe pins `-t 1` when it needs an exact assertion and drops to floors when it runs `-t 8` for speed. **If your code is this kind, you can assert bands, not numbers, on a multi-threaded run.**

- **The cost climbs** — [GROMACS](../recipes/gromacs/README.md). Throughput keeps rising to 192 cores, but efficiency erodes — 90–100% per doubling up to 96, then **59% across 96→192**, where two things change at once (both measured, below): the run starts spanning **two NUMA nodes** and atoms-per-rank halves. Cost-efficiency halves over the range. There's also an **interior optimum in the decomposition**: at 64 cores, `32×2` (ranks×threads) beat `8×8` by 17%, a 25% spread across all five layouts. **If your code is this kind, the physics is fixed — bigger just costs more per result, and how you split ranks vs threads is a real but second-order dial.**

- **It hits a wall** — [GPAW](../recipes/gpaw/README.md). Scales cleanly to 48 ranks (58% efficiency), then at 64 it doesn't slow down — it **fails**: the cell can't be divided further. The energy is identical to 3×10⁻⁵ eV whether you run 1 rank or 48. **If your code is this kind, scale ranks freely for speed and trust the result, but there's a hard ceiling — past it you get an error, not a slow run.**

"It got slower" and "it stopped working" need different responses. Knowing which kind of code you have is most of sizing.

## The launch dominates the decomposition

Before you tune anything, get the launch right — it is worth more than every decomposition choice combined. Running GROMACS as `mpirun -np 1 ... -ntomp 8` pins all 8 threads to **one core** by default and delivers **1.9 ns/day instead of 13.5 — a 7× loss** — because `mpirun`'s default binding gives one rank one core regardless of its thread count. That single flag error dwarfs the 25% you'd chase tuning ranks-vs-threads. **Give each rank its cores** (`--map-by slot:PE=<threads> --bind-to core`) and confirm it before optimizing anything else. Someone agonizing over `32×2` vs `16×4` while their launch costs them 7× is looking at the wrong dial.

## Find your knee: measure, don't guess

An afternoon's sweep answers it for your own input, and it's cheap and self-terminating:

```bash
# same input, same box, walk the core count — read the curve
for C in 8 16 32 64 96; do
  spawn task run --spec sweep-c$C.json   # cpu=C, one sensible decomposition, wall-bounded
done
```

Read **two** curves off it, not one:

1. **Throughput vs cores** — where does it flatten? That's the speed knee.
2. **Throughput per dollar** — this almost always peaks *earlier* than throughput. That's your economical size.

If you only need the wall-clock and the deadline is now, ride past the cost knee knowingly. Otherwise, the peak of the second curve is your answer. For a cohort of many runs, size *one* at its cost knee and fan out — see [Job arrays](job-arrays.md).

<details>
<summary>The measured curves, the decomposition table, and the topology check</summary>

### GROMACS, benchMEM (82k atoms), one c8g node, ntomp=4

| Cores | 8 | 16 | 32 | 48 | 64 | 96 | 192 |
|---|---|---|---|---|---|---|---|
| ns/day | 13.0 | 24.7 | 47.2 | 66.1 | 82.8 | 124.1 | 147.7 |
| ns/day per $/hr | 40.7 | 38.7 | 37.0 | 34.5 | 32.4 | 32.4 | **19.3** |

Efficiency is ~90–100% per doubling to 96, then 59% across 96→192. Two things change at 192, both measured, and this cookbook can't cleanly separate them: the 192-vCPU instance spans **two NUMA nodes** (`node0` = cores 0–95, `node1` = 96–191 — see the topology check below), and atoms-per-rank halves from ~3,400 to ~1,700. Graviton4 c8g is a **single-socket** part, so this is a NUMA-node crossing *within* the socket, not a socket boundary — don't mistake it for one. A **smaller** system would bend sooner from the atoms-per-rank side alone, because that half of the knee is set by work-per-rank, not cores. Note this is the *cost* knee — throughput never actually declines here. A visible throughput decline needs a system small enough to starve, which is not a size anyone reaches for in practice; don't manufacture one to make the point.

### Decomposition at 64 cores (same box, same work)

| ranks×threads | 64×1 | 32×2 | 16×4 | 8×8 | 4×16 |
|---|---|---|---|---|---|
| ns/day | 81.6 | **90.3** | 78.3 | 77.1 | 72.1 |

A 25% spread (90.3 down to 72.1) with an interior optimum at 2 threads/rank; `32×2` beats `8×8` by 17%. On this 64-vCPU box that's GROMACS's own PP/PME balance, **not** a NUMA effect — because a 64-vCPU c8g is one NUMA node (below). We assumed NUMA; the topology capture said otherwise *here*. That's the point of capturing it — and, as the 192-core case shows, of not assuming it stays the same at other sizes.

### Check the machine, don't assume it

Memory locality differs by instance family and generation, so read it, don't guess:

```bash
lscpu | grep -i numa       # NUMA node(s) and their CPU ranges
numactl --hardware         # node distances, if numactl is present
```

On a 64-vCPU Graviton4 c8g this reported **one NUMA node spanning all 64 cores** — so the decomposition spread above is the code's physics, not the hardware's layout. But a 64-vCPU c8g is a **partial host**, and the topology is not the same at every size: the full-host **192-vCPU c8g.48xlarge reports two NUMA nodes** (0–95 and 96–191), still a single-socket part. So the 64-core spread is one-node physics, while the 96→192 knee coincides with the run straddling two NUMA nodes. Partial and full hosts differ; that is exactly why you read the topology instead of assuming it — and why you don't assume it's constant across instance sizes. On a dual-socket x86 box it can look different again. Capture it in your own run and let the write-up say what the machine actually was.

### The launch-binding fix, in full

`mpirun -np R --map-by slot:PE=$((CORES/R)) --bind-to core gmx_mpi mdrun … -ntomp $((CORES/R))` — every rank gets its own cores. Without `--map-by … PE`, `mpirun` binds each rank to a single core and starves its OpenMP threads. Confirm with `--report-bindings` or by checking that `-np 1 -ntomp <all>` matches a direct (non-mpirun) run.

### These are throughput numbers, not "what the code costs"

They're for finding the *shape* of the curve, not a price list — boot, image pull, and staging still sit around every real run (see [The container path](../practices/container-path.md)). Read them as "where does this stop paying," not "GROMACS costs $X."

</details>

## Where this shows up

Every compute-bound recipe has a knee; a decision ladder (a planned page) will turn "which shape is my code, and how big is my problem" into a family and a size. And the whole argument for fanning out a cohort instead of buying one big node rests on sizing *one* task at its knee — [Job arrays](job-arrays.md).
