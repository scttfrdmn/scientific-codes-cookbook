# Do you need a GPU, and which one — matching the SKU to the workload

> **A GPU sells compute and memory bundled in a fixed ratio, and you pay for both whether your code uses them or not.** Small-system MD is compute-bound with a sub-gigabyte footprint, so the biggest cards are the wrong *ratio*, not merely more than you need. This is the same mistake made on-prem — the cloud just prices it per hour instead of burying it in a capital purchase.

Two readers ask this question and they need different answers: one is **cost-bound** ($/result), the other **deadline-bound** (time to answer). They can point opposite ways, so both are reported here. Rate × wall, billed-vs-compute, and the cross-build trap are [cost per result](cost-per-result.md)'s job; this page is about accelerators and the fraction of one you actually use.

## Measured: GROMACS and Amber, same box, same system

All runs: one ~20k-atom water box, 25 000 steps, PME, 2 fs, constrained H-bonds, 300 K. `$/ns` is on-demand rate ÷ ns-per-day. GPU util is the **steady-state plateau** from a 1 Hz series, not a mean — a mean over a short run is dominated by startup and will mislead in either direction.

| card / box | GROMACS ns/day | Amber ns/day | compute plateau | HBM used | $/hr | Amber $/ns |
|---|--:|--:|--:|--:|--:|--:|
| **L4** (g6.2xl) | 392.8 | **544.6** | 91% (Amber) | 0.4 GB of 23 | 0.978 | **$0.043** |
| L40S (g6e.2xl) | 489.7 | 768.5 | 88% (Amber) | 0.7 GB of 46 | 2.242 | $0.070 |
| A10G (g5.2xl) | 287.3 | — | ~60% (GROMACS) | 0.3 GB of 23 | 1.212 | — |

**The faster card is the worse buy per result.** L40S computes 1.41× faster than L4 and costs 1.62× more per result. The newest mid-card beat the older bigger one outright: L4 is both faster *and* cheaper than A10G.

**Amber saturates a mid-range GPU; GROMACS does not.** `pmemd.cuda` holds ~88–91% on both cards and is ~1.4× faster than GROMACS here — so for Amber a faster card buys real speedup and the decision is honestly price-per-result. GROMACS at ~60% has headroom to fill instead (below).

## The binding constraint decides — and memory usually isn't it

Amber uses **1.5% of an L4's HBM**. On an H200 the same run would touch **0.3% of 141 GB**. But "96% of memory wasted" is the wrong complaint: that memory is **unusable, not merely unused** — once compute saturates, extra memory buys nothing. Report effective cost against whichever resource *binds*; the other is stranded by construction, because you cannot buy L4 compute with 4 GB of HBM.

So the SKU's memory:compute ratio has to match the workload's. H100/H200 are memory-heavy parts built for memory-**bound** work (LLM weights, KV cache). Pointing one at a 20k-atom trajectory is paying for the wrong ratio. Memory becomes the binding constraint only when a single system genuinely doesn't fit, or when many co-resident replicas have compute to feed them.

## Fill the card before buying a bigger one

Nobody runs one system. Packing concurrent jobs onto one GPU with NVIDIA MPS converts idle silicon into throughput — GROMACS on one L4:

| N jobs | per-system ns/day | aggregate ns/day | compute util | $/ns |
|--:|--:|--:|--:|--:|
| 1 | 430.1 | 430.1 | 27% | $0.055 |
| 2 | 372.5 | **744.9** | 82% | **$0.032** |
| 3 | 252.5 | 757.4 | 91% | $0.031 |
| 4 | 189.6 | 758.2 | 94% | $0.031 |

Per-system throughput **degrades 2.3×** while aggregate **recovers 1.76×** and cost per result falls **42%**. The knee is sharp at **N=2**: past it you are slicing a saturated card into thinner pieces — latency lost, no throughput gained. Same shape as the CPU [scaling knee](sizing.md), one resource over: pack until saturation, then stop.

This is what makes the big-card question answerable. If a modest L4 saturates at N=2 on a 20k-atom system, a card 3–5× more capable needs **N ≈ 6–10 concurrent jobs** to fill. An H100/H200 is justified when you can keep it that full — or when one system is large enough to need the memory — and not otherwise.

## Time to answer is not compute time

A deadline-bound reader should note that wall clock has four parts: **acquisition + boot + setup + compute**, and only the last one appears in a benchmark.

- **Acquisition is the term no rate card shows.** Every CPU type here placed first-try in ~25 s. GPUs needed a capacity watch across multiple regions; L40S was catchable only in a third region, and the newest cards went unobtained for hours. A card you cannot get is infinitely slow, and that risk rises with card size.
- **Setup can dominate a short job.** A first Amber run spent **36 s before touching the GPU** — PTX JIT compiling a binary with no native cubin for the card — against 8 s of actual MD. The second run in the same instance started in **1 s**, because the JIT output caches.

That 36 s is per *fresh* instance, so it scales with campaign shape, not job count: a thousand one-job instances pay it a thousand times (~10 GPU-hours of recompiling). Bake the warmed cache into an image once the campaign is big enough to amortize building it — or better, compile for your card's native architecture and the cost disappears.

## What to take away

1. **Check utilization before buying capability.** A plateau under ~50% means the answer is packing or a smaller card, not a bigger one.
2. **Match the ratio.** Compute-bound with a small footprint → the cheapest card that saturates. Memory-bound → pay for HBM.
3. **Price the wait.** Include acquisition and setup, and remember an unobtainable card has infinite time-to-answer.
4. **Two axes, stated separately** — $/result and time-to-answer genuinely disagree here, and which one rules is the reader's constraint, not ours.

Numbers here are the small-system rung; utilization rises with system size, which moves the crossover. H100 and Blackwell rows are pending capacity and will be added when measured.
