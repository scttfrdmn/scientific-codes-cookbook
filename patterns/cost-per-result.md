# What the rate card costs you — $/result across instance families

> **A box that costs more per hour can cost less per result — and the easiest cross-family comparison to run is the easiest one to get wrong.** The rate card and the vCPU count both mislead; what a run costs is rate × wall — and only within one build is a cross-family comparison honest. Measured on this cookbook's assemblers across five families.

The shared-cluster instinct is to compare instances by price per hour, or by vCPU count. Both are the wrong number. What a run costs is **rate × wall time**, and because a newer or pricier core can finish sooner, the cheapest-per-hour box is routinely not the cheapest per result. But before any of that: the comparison itself has to be honest — and the most tempting cross-family headline is a trap.

## The trap, first: a cross-arch number compares builds, not just silicon

Run [SPAdes](../recipes/spades/README.md) on the same reads across families and x86 looks **~2.5× faster** than Graviton — an assembly headline waiting to be published. It is wrong, and the tell is in the cores actually used: the arm64 build averages **~3 cores**, the x86 build **~6**. That is not the chip, it is the **build** — arm64 SPAdes (the aarch.bio image) and x86 SPAdes (biocontainers) are different compiles with different threading behaviour, and the ~2.5× gap is mostly that, not the silicon. We know it is a stable build property and not noise because the ~3-core arm64 ceiling reproduced on a different day and different boxes: **3.37 / 3.10 / 3.00 cores here against 3.39 in the earlier sizing run.** [megahit](../recipes/megahit/README.md) hides it — its two builds parallelise alike (~6 cores each) — which is exactly why a single workload can't warn you.

So: **within one arch (one image across boxes) is a clean comparison; across arches you compare build channels as much as chips** — [compare like with like](../practices/cross-checks.md), the same rule that governs cross-code checks, applied to hardware. Everything trustworthy below is within-arch. "Graviton is 2.5× slower for assembly" would have been this project's worst claim: plausible, dramatic, and false.

## The clean result: newer generation, pricier per hour, cheaper per result

The sharpest within-arch comparison is a generation step — **c9g vs c8g**, same family, same image, same 8 cores, one generation apart. c9g is **+9%/hr** and finishes **~18% faster**, so it lands **~12% cheaper per result**. It holds on both assemblers (megahit −12%, SPAdes −13% compute / −20% billed), so it is a hardware property, not one run's luck. That is the cookbook's opening claim with a 2026-generation number behind it: **the pricier box wins the bill.**

## The rate card misleads in both directions

Two more within-family lessons, each a way the spec sheet lies:

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

**Topology** — `arch` and `threads/core` set what "8 vCPU" means:

| family | arch | vCPU | phys cores | threads/core | $/hr |
|---|---|---|---|---|---|
| c8g | arm64 | 8 | 8 | 1 | 0.319 |
| c9g | arm64 | 8 | 8 | 1 | 0.348 |
| m9g | arm64 | 8 | 8 | 1 | 0.391 |
| c8a | x86_64 | 8 | 8 | 1 | 0.431 |
| c8i | x86_64 | 8 | **4** | **2** | 0.375 |

**megahit** (~55 s compute — boot-dominated, so billed ≈ rate-ranked). Both builds parallelise alike (~6 cores), so the cross-arch rows *are* comparable here — unlike SPAdes below:

| family | arch | wall | phys-equiv cores | compute $/result |
|---|---|---|---|---|
| c9g | arm64 | 44.9 s | 6.44 | **0.0043** |
| m9g | arm64 | 42.1 s | 6.31 | 0.0046 |
| c8a | x86 | 38.7 s | 6.61 | 0.0046 |
| c8g | arm64 | 54.8 s | 6.41 | 0.0049 |
| c8i | x86 | 58.7 s | 3.34 | 0.0061 |

**SPAdes** (~1.5–4.5 min compute — billed tracks compute). **Read within an arch block only** — the arm64↔x86 gap is the build confound above (~3 vs ~6 cores), not silicon.

*arm64 — aarch.bio build (~3-core ceiling); the clean generation comparison:*

| family | wall | phys-equiv cores | compute $/result | billed |
|---|---|---|---|---|
| c9g | 212 s | 3.10 | 0.0205 | 0.0331 |
| m9g | 207 s | 3.00 | 0.0225 | 0.0372 |
| c8g | 266 s | 3.37 | 0.0236 | 0.0414 |

*x86 — biocontainers build (~6 cores), a different compile; do **not** read against the arm64 rows:*

| family | wall | phys-equiv cores | compute $/result | billed |
|---|---|---|---|---|
| c8a | 83.8 s | 5.67 | 0.0100 | 0.0265 |
| c8i | 111 s | 3.03 | 0.0116 | 0.0296 |

Memory was arch-independent on both workloads (~0.42 GiB megahit, ~4.6 GiB SPAdes anon) — footprint is data-structure-bound, so no family needs a memory box for these.

</details>

## Where this shows up

Any time you choose an instance family, this is the question underneath it. [Sizing](sizing.md) finds *how many cores* a run uses; this page is *which family's cores* are cheapest for the result. Measured on [megahit](../recipes/megahit/README.md) and [spades](../recipes/spades/README.md), which link here rather than restating it.
