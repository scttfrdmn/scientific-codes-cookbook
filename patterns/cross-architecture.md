# Graviton or x86: the same answer, and what it costs

The catalog runs on Graviton4 (arm64). A reader asking "can I run this on x86, and should I?" is really asking two questions — *do I get the same answer* and *what does it cost* — and only the first has one answer. Measured on three recipes across `c8g.large` (Graviton4) and `c8i.large` (8th-gen Intel) — **same generation**, us-west-2 — so the comparison is architecture, not a generation gap hidden inside it. This is the cross-architecture companion to [cost per result](cost-per-result.md).

## First result: the answer is the same on both

The physics is architecture-independent, and that outranks cost — a reader's first question about running elsewhere is "do I still get the right answer," not "what does it cost." OpenFOAM's lid-driven cavity, solved on both boxes from the *same* `opencfd` image, reached **machine-zero mass continuity on each** — cumulative `3.77e-19` (arm64) and `−8.23e-19` (x86) — with **identical max Courant `0.394`**. Same discretization, same solution, either chip. bwa produced the **identical** 808505-record alignment on both; gatk4 called **802 variants** on both. Correctness does not depend on the architecture.

## A cross-chip identity seen on a fixture is a claim about the fixture

"Same answer on both chips" holds for **fixed points**: converged energies, counts, conservation
totals, optima. Across the 149 recipes verified on both chips, none of those moved. It does not
extend to bytes along a **path**, and a small fixture can hide which one an output is. FSL's BET mask
was byte-identical across chips on the MNI152 template, but on 125 real NFBS scans only **102 of 125**
were, and FLIRT's recovered motion differed on all 125, by up to 0.15 mm, well inside its quarter-voxel
bound ([fsl-real](../measurements/fsl-real/README.md)). The template was simply too regular for
rounding to matter. So assert a cross-chip identity at the scale it will be used, or classify the
output as a path and bound it. Never carry a fixture's bit-equality into a claim about real data.

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

The real-workload batches found three more builds where the arm64 deficit comes from the toolchain, not the core. Each is measured, and each is fixable without new silicon:

| code | what arm64 ships | measured | |
|---|---|---|---|
| any pure-Python tool ([mageck](../measurements/mageck-real/README.md)) | conda-forge CPython cross-compiled from x86, so built **without PGO** | gzip line reading 2.56 M/s on Graviton4 vs 3.85 Intel, 5.02 AMD | [python-feedstock#955](https://github.com/conda-forge/python-feedstock/issues/955) |
| [IQ-TREE](../measurements/iqtree-real/README.md) | the 128-bit `SSE2` likelihood kernel (x86 gets `AVX+FMA`) | AMD 2.8× Graviton4 per iteration | |
| R's `stats::cor`/`cov` ([wgcna](../measurements/wgcna-real/README.md)) | `cov.c` accumulates in `LDOUBLE`, which is software 128-bit on aarch64 Linux (80-bit x87 hardware on x86) | `stats::cor` 29–55× slower, `sum`/`mean`/`colSums` 9–14×, while a `double` BLAS reduction is as fast as x86 ([measured](../measurements/r-longdouble/README.md)) | |

So when an arm64 number is far off, check how the arm64 build was made before you blame the chip.
