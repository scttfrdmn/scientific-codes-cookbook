---
tool: bismark-methyldackel
tool_version: "Bismark 3.1.0 / MethylDackel 0.6.1"
images:
  - quay.io/aarchbio/bismark@sha256:1bdd5895c5b9b458f8f4c1a19fd1b177f3ffab951262c37c543f2ce767150302
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/methyldackel@sha256:c75367a11f9943fe378fcf266859810617e3569f70acd8a55fcb2597a9996586
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# Methylation — two callers, and cytosines we methylated ourselves

Bismark aligns bisulfite reads and calls CpG methylation on Graviton4; MethylDackel then calls the *same* alignments independently, and both are checked against methylation that was chosen before the reads existed. The catalog's first methylation recipe, for anyone doing WGBS or RRBS.

> **What this covers.** A 1 kb reference containing exactly three CpGs, 30 directional single-end reads, methylation planted at 100% / 50% / 0%. Index preparation, alignment, and two independent extractions. Not RRBS trimming, paired-end, non-CpG contexts, deduplication, or real coverage.

## Run it

```bash
bismark prepare genome --bowtie2                              # CT + GA converted indices
bismark align --genome genome --single_end reads.fq            # directional bisulfite alignment
bismark extract -s --bedGraph --comprehensive reads_bismark_bt2.bam

samtools sort -o sorted.bam reads_bismark_bt2.bam && samtools index sorted.bam
MethylDackel extract --mergeContext chrS.fa sorted.bam -o md   # the second, independent caller
```

Three tasks: Bismark builds the fixture and calls methylation, samtools sorts and indexes, then MethylDackel calls the same alignments and the two are compared.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 1 kb reference with 3 planted CpGs | your genome | every other `CG` is deliberately removed, so the tools have exactly three sites they could possibly report — that is what makes "no spurious calls" assertable. |
| reads converted in-code | your WGBS FASTQs (after `trim_galore`) | real libraries need adapter/quality trimming first, and RRBS needs `--rrbs`; neither changes the shape below. |
| directional (default) | `--non_directional` | get this wrong and reads align poorly for reasons that look like bad data. |
| both callers | either one alone | they agree here, so pick on ergonomics — but see the coordinate note before you diff their outputs. |

**Leave the fixture:** 30 reads over three sites make every methylation count hand-checkable, which is what turns this into an exact assertion. **Scale it** to a real genome — `prepare` on a mammalian reference is hours and tens of gigabytes, not seconds.

## Shape, size, cost

Three tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 12–15m each, caps $0.05 each. Bismark's prepare → align → extract chain runs in about a minute at this size; the other two tasks are seconds of work inside an image pull. **These timings are not compute cost.**

<details>
<summary>As shipped: exact per-site counts, two-caller agreement, the coordinate trap, why the 0% site matters, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| reference CpGs | exactly 3 — every other `CG` removed | **3** |
| mapping | 100% unique, none ambiguous | **100.0%**, 0 non-unique |
| sites reported | exactly 3 per caller — no spurious calls | **3** / **3** |
| Bismark vs planted | per-site methylated/unmethylated **equal** the planted values | **exact** |
| MethylDackel vs planted | same | **exact** |
| the two callers | identical counts at every site | **agree** |
| conservation | Σ calls == read count | **30 of 30** |

Recovered by both, exactly:

```text
chrS  200  100%  10 meth /  0 unmeth      (planted 10/0)
chrS  400   50%   5 meth /  5 unmeth      (planted 5/5)
chrS  600    0%   0 meth / 10 unmeth      (planted 0/10)
```

### The coordinate trap — two agreeing tools that a naive diff calls different

The callers report the *same numbers* at *different coordinates*:

```text
Bismark .cov            chrS  200  200      1-based position of the C
MethylDackel bedGraph   chrS  199  201      0-based half-open span of the CpG
```

Compare the files directly and you get a total mismatch from two tools that agree perfectly. The recipe normalises first (`start + 1` → the 1-based C) and **asserts that the raw coordinates do *not* match**, so the convention difference is recorded as a fact rather than discovered as a mystery later. This is the same discipline as [matching the modes](../../practices/cross-checks.md) before comparing: the metric has to measure agreement, not a formatting difference.

### Why the 0% site is the most informative one

In bisulfite sequencing an unmethylated cytosine is read as **T**. A caller that treated those T's as sequencing mismatches rather than conversion events would either fail to align those reads or call the site methylated. Requiring `0 meth / 10 unmeth` asserts the conversion is being *interpreted*, not tolerated — and it is the case a fixture with only methylated sites would never exercise. Planting 100%, 50% and 0% covers the range instead of one convenient point.

**The conservation identity** works because each read covers exactly one CpG by construction, so total calls must equal the read count — catching reads silently dropped between alignment and extraction, which neither the mapping report nor the per-site percentages would reveal alone.

**Stripping the incidental CpGs** is what makes "no spurious calls" a claim rather than a coincidence: a random 1 kb sequence contains many `CG` dinucleotides, so the fixture removes them all and plants three.

### This is Bismark 3.x — the Rust rewrite

`bismark --version` reports *"Bismark Rust suite"*: **one binary with subcommands** (`prepare`, `align`, `extract`, `dedup`, …), with the classic script names kept as aliases. Older guides' flags may not exist — read `bismark <subcommand> --help` rather than assuming the Perl interface.

**Why three images.** Neither the Bismark image nor the MethylDackel image ships `samtools`, and MethylDackel requires a **coordinate-sorted, indexed** BAM while Bismark writes read-order output. So sorting is its own task in the samtools image — the [one tool per image](../../patterns/execution-shapes.md) model, with the BAM handed along through S3.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| Bismark | `quay.io/aarchbio/bismark@sha256:1bdd5895…` (3.1.0, bundling bowtie2) |
| samtools | `quay.io/aarchbio/samtools@sha256:11917396…` — the same pin [bwa-samtools](../bwa-samtools/README.md) uses |
| MethylDackel | `quay.io/aarchbio/methyldackel@sha256:c75367a1…` (0.6.1, HTSlib 1.21) |
| input | none — reference, planted methylation state and bisulfite-converted reads are generated in-task by awk from `srand(5)` |

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before any of these tools is callable.

### Run + verify

```sh
make run RECIPE=bismark
make ls  RECIPE=bismark
```

Assertions are `test` calls inside tasks 1 and 3 ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `callers_agree yes`, both `*_exact yes`, and `raw_coords_match no`.

</details>
