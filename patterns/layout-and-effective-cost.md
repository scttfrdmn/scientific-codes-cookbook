# What you actually pay for — layout, and why $/core-hour can't see it

> **$/core-hour is identical for every size in a family and rises across generations while the cost of real work falls.** `c8g.large` and `c8g.4xlarge` are both $0.03988/vCPU-hr — the metric cannot distinguish the sizes you choose between. Graviton2→5 is **+27.9% per core-hour** while $/result on real codes drops **33–45%**. It's constant where you need discrimination and backwards where you need a reversal.

Everyone compares it because it's what procurement reports and chargeback uses, and it never contradicts itself — because it can't. Everything below is one NWChem calculation (caffeine, B3LYP/6-31G\*, 217 basis functions), same digest, same input, on Graviton4.

## Layout dominates everything else

The realistic unit is a campaign, not one run. Ten thousand of these calculations:

| shape | cost |
|---|---|
| **pack one `c8g.4xlarge`, 16 jobs × 1 rank** | **$16** |
| best right-sized single job (2 ranks, `c8g.large`, sequential) | $25 |
| fan out the cheapest shape (10,000 launches) | $57 |
| fan out the *fastest* shape (10,000 × 16 ranks) | **$300** |

**An 18× spread, and ranks-per-job accounts for none of it.** The fastest configuration is the worst thing to fan out: you pay the provisioning tax ten thousand times to buy 24 s of compute each time, on the dearest box.

## Busy is not useful

Filling one 16-core box five ways:

| layout | wall | util | s/result | **core-s/result** | $/10,000 |
|---|---|---|---|---|---|
| 16 × 1 | 147 s | 99.2% | 9.19 | **145.8** | **$16** |
| 8 × 2 | 110 s | 98.8% | 13.75 | 217.4 | $24 |
| 4 × 4 | 72 s | 97.8% | 18.00 | 281.5 | $32 |
| 2 × 8 | 42 s | 98.1% | 21.00 | 329.5 | $37 |
| 1 × 16 | **25 s** | 93.5% | 25.00 | 374.0 | $44 |

**Every layout keeps the box 97–99% busy and they differ 2.7× in cost.** Utilization is necessary, not sufficient: `1×16` burns 2.6× the core-seconds per answer on parallel overhead rather than chemistry. Effective cost is **paid ÷ useful**, not paid ÷ busy — and here the fastest layout is the dearest.

## Two knees, and they disagree

Varying ranks for a single job, same box:

| ranks | 1 | 2 | 4 | 8 | 16 |
|---|---|---|---|---|---|
| wall | 128 s | 100 s | 67 s | 41 s | 24 s |
| efficiency | 100% | 64% | 48% | 39% | **33%** |
| marginal per doubling | — | 1.28× | 1.49× | 1.63× | **1.71×** |

Efficiency says "don't bother" at 33% while the last doubling returns the *best* marginal gain of the sweep. Efficiency is speedup ÷ ranks, so it always eventually looks bad and never answers "should I add more ranks." **The speed knee is beyond 16; the cost knee is at 2.**

## The provisioning tax, itemised — and the AMI question

Measured from spawn's phase stamps, per instance launch:

| component | time | fixable? |
|---|---|---|
| pending → running | 5 s | unbilled |
| **boot + spored start** | **9 s** | no — and it is *not* the problem |
| **docker install** | **27–45 s** | yes, bake it |
| image pull | 4–31 s | yes, cache it |
| stage-in | 0–13 s | partly — [data path](data-movement.md) |

"Bake an AMI" is the standard advice and it optimises the launch you shouldn't be making. A 12 GB snapshot costs ~$0.60/month and saves ~70 s per launch:

- **fan out 10,000 jobs** → saves $124. The AMI pays for itself 207× over.
- **pack one box** → saves **1.2 cents**, for $0.60/month. It never pays.

**The AMI is only worth it in the shape you should not be using** — fix the layout and its benefit evaporates. There is also a correctness argument here: recipes pin containers by `@sha256:` digest, and an AMI with pre-pulled layers is a second source of truth that can drift from the pin silently.

## What forces the box — and what you can do about it

Stranding is the on-prem problem (a node's core:memory ratio is welded in) and the cloud does not remove it — it lets you **shape** the bundle. Measured in this catalog:

| recipe | what binds | move | result |
|---|---|---|---|
| [fastp](../recipes/fastp/README.md) | staging footprint | `c8g.4xlarge` → `m8g.2xlarge` | 44% cheaper, 16 unusable cores shed |
| [flye](../recipes/flye/README.md) | assembler working set | `m8g` → `c8g.2xlarge` | 11% cheaper, idle RAM shed |
| [kraken2](../recipes/kraken2/README.md) | index + its compressed copy | forced to 64 GiB | 8 vCPU arrive unneeded |

The residue has two sources worth separating. **Granularity**: nothing smaller than 2 vCPU exists, so a 1-rank job strands half a `c8g.large` (measured: 1.00 of 2 cores busy). **Capability gating**: some capabilities force a *size*, not a shape —

```text
smallest EFA-capable size:  c8g.24xlarge (96 vCPU)   c9g.48xlarge (192 vCPU)
```

so a multi-node job needing EFA must rent 96 or 192 vCPU per node, and the newer generation **doubles** the floor. Check `NetworkInfo.EfaSupported` before planning a campaign; that single fact can dominate the cost model.

When the size is forced, three responses in order of availability: **right-size** (if cores bind), **pack homogeneously** (the campaign case above), or **pipeline heterogeneously** — spend the leftover cores on *different* work. Whether the third is free depends on which knee you hit:

| knee | symptom in a concurrency sweep | pipelining? |
|---|---|---|
| communication / decomposition | throughput keeps rising with concurrency | **free** — the spare cores still have bandwidth |
| memory bandwidth | throughput plateaus below the core count | no — nothing left to give |

## A sharp edge that mimics bandwidth saturation

Packing concurrent MPI jobs, each `mpiexec` believes it owns the machine and binds rank *k* to core *k*. Eight concurrent 2-rank jobs put 16 processes on **2 cores**: measured **2342 s at exactly 2.00 cores busy**, against 110 s at 15.81 for the same work with `--bind-to none`. A 21× slowdown that looks exactly like contention — and the only tell is the *integral* core count, which nobody reads. [GROMACS launch binding](sizing.md) is a 7× lever for the same reason.

## Where this shows up

Before you compare instance prices. [cost-per-result](cost-per-result.md) is which family's cores are cheapest; [sizing](sizing.md) is how many cores one run uses; [gpu-tradeoff](gpu-tradeoff.md) is the same argument where the bundle is welded shut. **Open gap:** the GPU table reports SM occupancy and HBM but not host cores or host RAM, so three of four rented resources are invisible there — it needs a card view *and* a system view before "the GPU is busy" can mean "the instance was worth renting."
