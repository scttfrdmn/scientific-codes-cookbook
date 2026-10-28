# What you actually pay for — layout, and why $/core-hour can't see it

> **$/core-hour is identical for every size in a family and rises across generations while the cost of real work falls.** `c8g.large` and `c8g.4xlarge` are both $0.03988/vCPU-hr, so the metric cannot tell apart the sizes you choose between. Graviton2→5 is **+27.9% per core-hour** while compute $/result on the ten codes with a full ladder drops **31–52%** ([which comparison applies](cost-per-result.md)). It is constant where you need discrimination and backwards where you need a reversal.

Everyone compares it because it is what procurement reports and chargeback uses, and it never contradicts itself, because it can't. The numbers below are one NWChem calculation (caffeine, B3LYP/6-31G\*), same digest, same input, on Graviton4: [nwchem-real](../measurements/nwchem-real/README.md) owns them.

## Layout dominates everything else

The realistic unit is a campaign, not one run. For ten thousand of these calculations ([the recipe's table](../recipes/nwchem/README.md#which-box--and-the-layout-matters-more)):

| shape | cost |
|---|---|
| **pack one `c8g.4xlarge`, 16 jobs × 1 rank** | **$16** |
| best right-sized single job (2 ranks, `c8g.large`, sequential) | $25 |
| fan out the cheapest shape (10,000 launches) | $57 |
| fan out the *fastest* shape (10,000 × 16 ranks) | **$300** |

**An 18× spread, and ranks-per-job accounts for none of it.** The fastest configuration is the worst thing to fan out: you pay the provisioning tax ten thousand times to buy 24 s of compute each time, on the dearest box.

## Busy is not useful

Five ways to fill one 16-core box all keep it **97–99% busy**, yet they differ **2.7× in cost**: `1×16` burns 2.6× the core-seconds per answer of `16×1` on parallel overhead rather than chemistry ([packing table](../measurements/nwchem-real/README.md#3-packing-272-the-throughput-for-almost-nothing)). Utilization is necessary, not sufficient. Effective cost is **paid ÷ useful**, not paid ÷ busy, and here the fastest layout is the dearest.

## Two knees, and they disagree

One job, ranks varied 1→16 on the same box: efficiency falls to 33% while the marginal return per doubling *rises* to 1.71× ([rank sweep](../measurements/nwchem-real/README.md#1-one-calculation-it-scales-but-badly-and-that-is-the-point)). Efficiency is speedup ÷ ranks, so it always eventually looks bad and never answers "should I add more ranks." **The speed knee is beyond 16; the cost knee is at 2.**

## The provisioning tax, itemised — and the AMI question

From spawn's phase stamps on the NWChem launches, per instance:

| component | time | fixable? |
|---|---|---|
| pending → running | 5 s | unbilled |
| **boot + spored start** | **9 s** | no, and it is *not* the problem |
| **docker install** | **27–45 s** | yes, bake it |
| image pull | 4–31 s (2m24s for the `hep` env: [fastjet](../recipes/fastjet/README.md)) | yes, cache it |
| stage-in | 0–13 s | partly: [data path](data-movement.md) |

"Bake an AMI" is the standard advice, and it optimises the launch you shouldn't be making. A 12 GB snapshot costs ~$0.60/month and saves ~70 s per launch:

- **fan out 10,000 jobs** → saves $124. The AMI pays for itself 207× over.
- **pack one box** → saves **1.2 cents**, for $0.60/month. It never pays.

**The AMI is only worth it in the shape you should not be using.** Fix the layout and its benefit evaporates. There is also a correctness argument: recipes pin containers by `@sha256:` digest, and an AMI with pre-pulled layers is a second source of truth that can drift from the pin silently.

## What forces the box — and what you can do about it

Stranding is the on-prem problem (a node's core:memory ratio is welded in), and the cloud does not remove it. It lets you **shape** the bundle:

| recipe | what binds | move | result |
|---|---|---|---|
| [fastp](../measurements/fastp-real/README.md) | staging footprint | `c8g.4xlarge` → `m8g.2xlarge` | 41% cheaper per run, 8 unusable vCPU shed |
| [flye](../recipes/flye/README.md) | assembler working set | `m8g` → `c8g.2xlarge` | 11% cheaper, idle RAM shed |
| [kraken2](../measurements/kraken2-real/README.md) | index + its compressed copy | forced to 64 GiB | 8 vCPU arrive unneeded |

The residue has two sources worth separating. **Granularity**: nothing smaller than 2 vCPU exists, so a 1-rank job strands half a `c8g.large` (NWChem's 1-rank row: 1.00 of 2 cores busy). **Capability gating**: some capabilities force a *size*, not a shape:

```text
smallest EFA-capable size:  c8g.24xlarge (96 vCPU)   c9g.48xlarge (192 vCPU)
```

So a multi-node job needing EFA must rent 96 or 192 vCPU per node, and the newer generation **doubles** the floor. Check `NetworkInfo.EfaSupported` before planning a campaign; that single fact can dominate the cost model.

When the size is forced, there are three responses, in order of availability: **right-size** (if cores bind), **pack homogeneously** (the campaign case above), or **pipeline heterogeneously**, spending the leftover cores on *different* work. Whether the third is free depends on which knee you hit:

| knee | symptom in a concurrency sweep | pipelining? |
|---|---|---|
| communication / decomposition | throughput keeps rising with concurrency | **free**: the spare cores still have bandwidth |
| memory bandwidth | throughput plateaus below the core count | no: nothing left to give |

## A sharp edge that mimics bandwidth saturation

Packing concurrent MPI jobs, each `mpiexec` believes it owns the machine and binds rank *k* to core *k*. Eight concurrent 2-rank jobs put 16 processes on **2 cores**: **2342 s at exactly 2.00 cores busy**, against 110 s at 15.81 with `--bind-to none` ([nwchem](../recipes/nwchem/README.md#packing-concurrent-jobs---bind-to-none-is-load-bearing)). A 21× slowdown that looks exactly like contention, and the only tell is the *integral* core count, which nobody reads. GROMACS's default launch binding is a 7× loss for the same reason ([gromacs-scaling](../measurements/gromacs-scaling/README.md)).

## Where this shows up

Before you compare instance prices. [cost-per-result](cost-per-result.md) is which family's cores are cheapest; [sizing](sizing.md) is how many cores one run uses; [gpu-tradeoff](gpu-tradeoff.md) is the same argument where the bundle is welded shut. **Open gap:** the GPU table reports SM occupancy and HBM but not host cores or host RAM, so three of four rented resources are invisible there. It needs a card view *and* a system view before "the GPU is busy" can mean "the instance was worth renting."
