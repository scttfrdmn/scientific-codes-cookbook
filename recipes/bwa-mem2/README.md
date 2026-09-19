---
tool: bwa-mem2
tool_version: "2.3"
image: quay.io/aarchbio/bwa-mem2@sha256:f9759b09a39aab57d879babbcb5a876a9ed4a55a698eb10b476f70a9cec81c15
images:
  - quay.io/aarchbio/bwa@sha256:19f0eceab80740b821be7ada082d4434acf778912aac658dd1b4c6692dd2e9ba
  - quay.io/aarchbio/bwa-mem2@sha256:f9759b09a39aab57d879babbcb5a876a9ed4a55a698eb10b476f70a9cec81c15
spawn_version: 0.111.1
last_verified: 2026-09-19
---
# bwa-mem2 — the faster BWA, proved identical to it

bwa-mem2 aligns the same paired reads as [bwa](../bwa-samtools/README.md) with a re-engineered implementation, on Graviton4. For anyone already running `bwa mem` who wants the speedup without changing their answer.

> **What this covers.** `bwa-mem2 mem` on 400 000 read pairs against GRCh38 chr20, checked against `bwa mem` on the *same staged bytes* with the same flags. Not a benchmark of either aligner — timings here are not compute cost.

## Run it

```bash
bwa-mem2 index chr20.fa                                    # one index, ~3x the size of bwa's
bwa-mem2 mem -t 8 -R "@RG\tID:smoke\tSM:HG00096" \
  chr20.fa reads_1.fq.gz reads_2.fq.gz > aln.sam           # same arguments as bwa mem
```

Two tasks: `bwa` writes the baseline alignment, then `bwa-mem2` writes its own and asserts the two agree exactly.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| chr20 + 400k pairs (the [bwa recipe's](../bwa-samtools/README.md) staged fixture, not a copy) | your reference + reads | the point is that two aligners read *identical bytes*; a second copy would weaken the comparison, not strengthen it. |
| `bwa-mem2 index` | a prebuilt index | **measured: 338 MB where bwa's is 113 MB — 3.0x.** Index once and reuse; budget the RAM and the disk before you swap `bwa` for `bwa-mem2` in a pipeline. |
| `-t 8` | your core count | scales with *physical* cores, so check what your instance really has — see [sizing](../../patterns/sizing.md). |

**Leave the fixture:** 400k pairs is enough for the identity to be a real assertion (800 000 primary alignments compared, not sampled) and small enough to finish in ~90 s. **Scale it** to your own reference — the index-size multiplier above is the thing that changes, and it is the reason to think before adopting.

## Shape, size, cost

Two tasks on `c8g.2xlarge` (8 vCPU / 16 GiB), TTL 20m + 25m, caps $0.10 + $0.12. Recorded windows: **2m15s** for the bwa baseline, **1m27s** for bwa-mem2 + the comparison. Staging is host `/tmp` (tmpfs, [half of RAM](../../patterns/sizing.md)) and peaks near 1 GiB with both SAMs and the index resident. **These timings are not compute cost.**

<details>
<summary>As shipped: the exact identity, the SIMD asymmetry, pins, smoke check, run + verify</summary>

### The check: exact identity, not a concordance band

bwa-mem2 is *designed* to reproduce `bwa mem`'s output. This recipe asserts that rather than assuming it, and the assertion is exact — no MAPQ gate, no tolerance:

| observable | assertion | observed |
|---|---|---|
| primary alignments, bwa | == 800000 | 800000 |
| primary alignments, bwa-mem2 | == 800000 | 800000 |
| identity on (chr, pos, MAPQ, flag) | byte-identical for **every** primary | **identical, 0 differing bytes** |
| arm64 from inside | `bwa-mem2` ELF `e_machine` == 183 | 183 (AArch64) |

That is a stronger check than the catalog's other aligner pair: minimap2↔bwa needed `MAPQ>=30` and a 5 bp window to reach 0.9921, because two *different* algorithms break repeat ties differently ([cross-checks](../../practices/cross-checks.md)). Here the claim is reimplementation fidelity, so the honest metric is equality — and equality is what it delivers, on all 800 000.

Comparison keys are built with portable arithmetic bit tests (`int(flag/256)%2`) rather than `and()`, which is a gawk extension absent from the image.

### The x86/arm64 build asymmetry — why a cross-arch number here needs a caveat

The two channels ship *structurally different* builds of the same version:

- **x86** (`quay.io/biocontainers/bwa-mem2`) ships six binaries — `sse41`, `sse42`, `avx`, `avx2`, `avx512bw` and a dispatcher that picks one at run time.
- **arm64** (`quay.io/aarchbio/bwa-mem2`) ships **one** portable binary, no dispatch.

So an arm64-vs-x86 timing of this tool measures a hand-tuned SIMD path against a portable one, not silicon — the same shape as gatk4's Intel GKL, and it is reported as an *observation* rather than a rate on [the cross-architecture page](../../patterns/cross-architecture.md).

### Pins (data tier: derived from the bwa recipe's staged fixture)

| | |
|---|---|
| bwa | `quay.io/aarchbio/bwa@sha256:19f0ece…` (0.7.19-r1273) |
| bwa-mem2 | `quay.io/aarchbio/bwa-mem2@sha256:f9759b09…` |
| inputs | `inputs/bwa-samtools/{chr20.fa, HG00096_chr20smoke_{1,2}.fq.gz}`, sha256-verified inside both tasks |

**Version note:** the conda package and image tag say **2.3**, but the binary self-reports **2.2.1** — on *both* channels, which is what makes the cross-arch pair version-matched. Frontmatter carries the package version; the smoke check records what the binary says. Trust the tool, not the tag.

**PATH note:** spawn's task shell does not inherit the image's `PATH`, so conda images need `export PATH=/opt/conda/bin:$PATH` before the tool is callable. `bwa` happens to sit on the default PATH; `bwa-mem2` does not.

### Run + verify

```sh
make run RECIPE=bwa-mem2
make ls  RECIPE=bwa-mem2
```

The smoke check runs inside the task and fails it ([exit 0 isn't proof](../../practices/container-path.md)); the bucket listing is the second half. Expect `smoke-check.txt` with `identical_primaries yes` and `differing_bytes 0`. Re-run overwrites this prefix — no spec edit needed.

</details>
