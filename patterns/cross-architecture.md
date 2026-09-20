# Graviton or x86: the same answer, and what it costs

The catalog runs on Graviton4 (arm64). A reader asking "can I run this on x86, and should I?" is really asking two questions — *do I get the same answer* and *what does it cost* — and only the first has one answer. Measured on three recipes across `c8g.large` (Graviton4) and `c8i.large` (8th-gen Intel) — **same generation**, us-west-2 — so the comparison is architecture, not a generation gap hidden inside it. This is the cross-architecture companion to [cost per result](cost-per-result.md).

## First result: the answer is the same on both

The physics is architecture-independent, and that outranks cost — a reader's first question about running elsewhere is "do I still get the right answer," not "what does it cost." OpenFOAM's lid-driven cavity, solved on both boxes from the *same* `opencfd` image, reached **machine-zero mass continuity on each** — cumulative `3.77e-19` (arm64) and `−8.23e-19` (x86) — with **identical max Courant `0.394`**. Same discretization, same solution, either chip. bwa produced the **identical** 808505-record alignment on both; gatk4 called **802 variants** on both. Correctness does not depend on the architecture.

## Second: cost — three results, three grades of cleanliness

A cross-arch cost number is only as trustworthy as its conditions, and these three carry *different* confidence. Stating which grade each result holds is the point — most benchmark content presents everything at one confidence level:

| recipe | grade | compute rate | compute-only $/result | billed $/result |
|---|---|---|---|---|
| **bwa mem** | **true microarch rate** — same `0.7.19-r1273` build path, matched **1.99 cores** both, identical output | arm64 **1.14× faster** | **arm64 1.34× cheaper** | arm64 1.17× cheaper |
| **OpenFOAM** | **control** — one upstream image, two digests, *no* build-channel confound | x86 **1.25× faster** | x86 1.07× cheaper | arm64 1.03× cheaper |
| **gatk4 HC** | **observation, not a rate** (caveat below) | x86 **~1.4× faster** | x86 1.20× cheaper | arm64 1.04× cheaper |
| **bwa-mem2** | **observation, not a rate** — hand-tuned x86 SIMD (`avx512bw`) vs a portable arm64 build; *equal work verified* (808505 records, 800000 primaries, identical index bytes on all three) | AMD **1.88× faster** than Graviton, Intel 1.26× | AMD 1.39× cheaper; **Intel only 1.07× cheaper** | — |

**gatk4's caveat, inline not footnoted:** its x86 speedup is real but *not* a clean per-core rate. It comes from the native **Intel GKL** pairHMM (arm64 falls back to the Java implementation) *plus* arch-dependent JVM threading (GC, async I/O), so `avg_cores` differs — **1.47 arm64 vs 1.92 x86** — even at matched pairHMM threads. Pinning to one core would measure a GATK nobody runs. The usable fact: **x86 wins where a hand-tuned native library exists** — the Intel GKL is worth something specific for GATK at scale, and it's why an arm64 GATK's numbers differ.

## Reading the two cost columns

They answer different questions, so both are reported:

- **Compute-only $/result** is the architecture *rate* — the tool's own compute time × the hourly, boot and image-pull excluded. It **splits by code**: bwa (integer/SSE alignment) favors Graviton, OpenFOAM (FP pressure solve) and gatk4 (native pairHMM) favor x86. **There is no blanket winner** — it depends on what the code stresses.
- **Billed $/result** is what you actually pay — the whole instance lifetime × the hourly, overhead included. Here **arm64 is cheaper or level on all three**: `c8i` costs ~17% more per hour, and with fixed boot/pull overhead diluting x86's compute edge (compute was ~55% of the billed window here, not 95%), the Graviton box wins or ties on the bill even where x86 computes faster.

## Three vendors, one code: the AMD dimension

Batch 1 paired Graviton against Intel only. Adding AMD to [bwa-mem2](../recipes/bwa-mem2/README.md) — same generation, same 8 vCPU, same version, equal work verified — changes the ranking and the reason for it:

| 8 vCPU, `c8*.2xlarge` | physical cores | SIMD path | align | $/align |
|---|---|---|--:|--:|
| c8g Graviton4 | **8** | portable, no dispatch | 22.6 s | $0.00200 |
| c8i Intel Xeon 6975P-C | **4** + SMT | `avx512bw` | 18.0 s | $0.00187 |
| c8a AMD EPYC 9R45 | **8** | `avx512bw` | **12.0 s** | **$0.00144** |

**AMD wins on both axes** — 8 real Zen5 cores plus the hand-tuned path. But the result worth pausing on is second place: **Graviton4 is within 7% of Intel on $/result while running a build with no SIMD dispatch at all**, against Intel's `avx512bw`. What you rent differs too — at "8 vCPU" Graviton and AMD give 8 physical cores, Intel gives 4 plus SMT ([sizing](sizing.md)).

The same run carries its own control: `bwa-mem2 index` is nearly arch-neutral (14.0–16.0 s, a 1.14× spread) while `align` spreads 1.88×. Same tool, same boxes — so the spread belongs to the SIMD-heavy phase, not to the machines in general. And `avg_cores` sits at 4.8–6.1 despite `-t 8`, *lower on the faster chips*: at this problem size the phase is partly serial-bound, so more cores would not pay proportionally.

## The build-confound, carried forward

The dramatic cross-arch number is the one to distrust — [SPAdes looked 2.5× faster on x86 and it was the *build*](cost-per-result.md) (arm64 ~3 cores vs x86 ~6), not the silicon. So this set was chosen for clean attribution and instrumented to prove it: **OpenFOAM shares one upstream image** (no channel to confound); **bwa and gatk4 are version-matched** across aarch.bio/biocontainers, with actual cores confirmed by `cpu.stat`, not assumed from the `-t` flag. **GROMACS and SPAdes were excluded** — GROMACS has no version-matched x86 build (2026.3 vs biocontainers' 2022), and SPAdes' confound is already on record — because a comparison you can't cleanly attribute measures the build channel, not the chip.
