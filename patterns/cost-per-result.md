# What the rate card costs you — $/result across instance families

> **A box that costs more per hour can cost less per result — and the easiest cross-family comparison to run is the easiest one to get wrong.** The rate card and the vCPU count both mislead; what a run costs is rate × wall — and only within one build is a cross-family comparison honest. Measured on 14 Graviton generation ladders, and on two assemblers across five families.

## The trap, first: a cross-arch number compares builds, not just silicon

Run [SPAdes](../recipes/spades/README.md) across families and x86 looks **~2.5× faster** than Graviton — a publishable-looking headline that is wrong. The tell is cores actually used: the arm64 build averages **~3**, the x86 build **~6**. That is the *build*, not the chip — two different compiles with different threading — and it is stable, not noise: the ~3-core arm64 ceiling reproduced at 3.37 / 3.10 / 3.00 against 3.39 in an earlier run. [megahit](../recipes/megahit/README.md) hides it, its two builds parallelising alike, which is why one workload cannot warn you.

So: **within one arch is a clean comparison; across arches you compare build channels as much as chips** — [compare like with like](../practices/cross-checks.md) applied to hardware. Everything below is within-arch. "Graviton is 2.5× slower for assembly" would have been this project's worst claim: plausible, dramatic, false.

## The ladder, measured: newer is cheaper per result, and one rung often does not pay

Query 8 of [`scripts/record-queries.sh`](../scripts/record-queries.sh) lists every Graviton2→5
generation series in [the record](../practices/measurement-record.md): **14 codes, each on one image
digest with an equal-work check passing on every rung.** Ten also hold the staged input; AmberTools,
GPAW, SIESTA and RAxML-NG hold image, size and an identical result, but carry no input key.
(GROMACS 2.43× and LAMMPS 2.24× have ladders on their recipe pages that the record does not hold yet.)

**Every one gets cheaper per result across the ladder.** Compute $/result falls 31% to 52%
(28% to 51% billed) on the ten that carry $, because `$/hr` rises 27–28% while wall falls 46–62%.
The four without $ in the record fall by similar amounts in their own tables: GPAW 45%, SIESTA 31%,
RAxML-NG 37%, [flye](../recipes/flye/README.md) 39%. The pricier box wins the bill.

The *size* of the win is set by the inner loop, not the field. Speedups run from **1.84×**
([bwa](../recipes/bwa-samtools/README.md)) to **2.65×** ([freebayes](../recipes/freebayes/README.md)),
and "floating-point codes gain more" does not survive the table. [SIESTA](../recipes/siesta/README.md) is
DFT and is second from the bottom (1.86×). [picard](../recipes/picard/README.md) MarkDuplicates, a JVM
sort-and-hash, beats it (1.93×). The top three are a variant caller, an MD engine
([AmberTools](../recipes/ambertools/README.md), 2.50×) and a single-threaded peak caller
([MACS2](../recipes/macs2/README.md), 2.40×). **Ask what the inner loop is, not what the field is called.**

### The Graviton3→4 rung

It is the smallest of the three steps in 11 of the 14. freebayes, MACS2 and picard are the exceptions:
for them Graviton4→5 is the smallest. On a compute basis the rung is cost-negative in four codes:
[hmmer](../recipes/hmmer/README.md) +4% (record), [RAxML-NG](../recipes/raxml-ng/README.md) +7.7%,
[GPAW](../recipes/gpaw/README.md) +1.4% and SIESTA +0.7% (their tables). The smallest saving among the
rest is salmon's 2%. GPAW and SIESTA are a tie at n = 1; either way, on Graviton3 in these four codes
the rung worth paying for is Graviton5. Why the step is weak is not established; it would need a
memory-bandwidth measurement.

## The rate card misleads in both directions

- **c8i looks fine per vCPU and isn't.** Its "8 vCPU" is **4 physical cores** (SMT, 2 threads/core) where every other family's 8 vCPU is 8 cores. On megahit it is only ~7% slower despite half the cores — the work isn't purely core-bound — but at +17%/hr that is **+24% per result**. The rate, not the core deficit, is what costs you.
- **c8a wins the clock and loses the bill.** AMD is the *fastest* wall on both codes (8 quick cores), but its +35%/hr rate puts it behind c9g per result. Fastest ≠ cheapest.

**Report cores-used with its SMT caveat.** The cgroup counts *logical*-CPU time, so c8i's "6.7 cores" is ~3.3 physical-core-equivalents (divide by threads-per-core). Put both numbers on the page — the raw and the normalised — or the comparison silently counts SMT threads as whole cores.

## Billed vs compute: job length decides which comparison applies

Boot, image pull, and staging are a **fixed overhead** on every run ([the container path](../practices/container-path.md)) — a couple of minutes regardless of family — so which cost comparison is real depends on how long the job runs:

- **Short jobs are boot-dominated.** megahit's ~55 s of compute sits inside ~4 min of billed instance time, so **billed is nearly flat across families and tracks the rate card** — for a one-off short run, buy the cheapest per hour.
- **Long jobs pay for the hardware.** SPAdes' minutes of compute make boot a minor share, so **billed ranks the same as compute-per-result** — the fast box wins the bill outright.

The rule: **a short run pays for boot; a long run — or many runs amortising boot — pays for the hardware.** Size the comparison to the job you are actually running.

<details>
<summary>The measured numbers: five families, two workloads, billed and compute</summary>

Measured us-west-2, on-demand, on boxes deliberately over-provisioned so the tool was never throttled (the box is the instrument, not the recommendation). Compute-only = rate × compute-wall; billed = rate × instance-uptime (launch→terminate). n = 1 per cell; the c9g < c8g result is the one that repeats across both workloads. Cross-arch rows carry the build confound above.

**megahit** (~55 s compute, boot-dominated, so billed ≈ rate-ranked). Both builds parallelise alike (~6 cores), so here the cross-arch rows *are* comparable. `cores` is physical-core-equivalent; c8i's 8 vCPU are 4 cores × 2 threads, every other family's are 8 cores × 1:

| family | arch | $/hr | wall | cores | compute $/result |
|---|---|---|---|---|---|
| c9g | arm64 | 0.348 | 44.9 s | 6.44 | **0.0043** |
| m9g | arm64 | 0.391 | 42.1 s | 6.31 | 0.0046 |
| c8a | x86 | 0.431 | 38.7 s | 6.61 | 0.0046 |
| c8g | arm64 | 0.319 | 54.8 s | 6.41 | 0.0049 |
| c8i | x86 | 0.375 | 58.7 s | 3.34 | 0.0061 |

**SPAdes** (~1.5–4.5 min compute, billed tracks compute). **Read within an arch block only**: arm64 is the aarch.bio build (~3-core ceiling), x86 the biocontainers build (~6 cores), a different compile.

| family | arch | wall | cores | compute $/result | billed |
|---|---|---|---|---|---|
| c9g | arm64 | 212 s | 3.10 | 0.0205 | 0.0331 |
| m9g | arm64 | 207 s | 3.00 | 0.0225 | 0.0372 |
| c8g | arm64 | 266 s | 3.37 | 0.0236 | 0.0414 |
| c8a | x86 | 83.8 s | 5.67 | 0.0100 | 0.0265 |
| c8i | x86 | 111 s | 3.03 | 0.0116 | 0.0296 |

Memory was arch-independent on both workloads (~0.42 GiB megahit, ~4.6 GiB SPAdes anon) — footprint is data-structure-bound, so no family needs a memory box for these.

</details>

## Where this shows up

Any time you choose an instance family, this is the question underneath it. [Sizing](sizing.md) finds *how many cores* a run uses; this page is *which family's cores* are cheapest for the result. Every recipe with a generation table links here rather than restating it; query 8 is the current list, and [megahit](../recipes/megahit/README.md) and [spades](../recipes/spades/README.md) carry the original within-family work.
