---
tool: bismark
tool_version: "3.1.0"
image: quay.io/aarchbio/bismark@sha256:1bdd5895c5b9b458f8f4c1a19fd1b177f3ffab951262c37c543f2ce767150302
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# Bismark — methylation calls checked against cytosines we methylated ourselves

Bismark aligns bisulfite reads and calls CpG methylation on Graviton4, checked against a reference whose methylation state was chosen before the reads existed. The catalog's first methylation recipe, for anyone doing WGBS or RRBS.

> **What this covers.** A 1 kb reference containing exactly three CpGs, 30 directional single-end reads, methylation planted at 100% / 50% / 0%. Index preparation, alignment and methylation extraction. Not RRBS trimming, paired-end, non-CpG contexts, deduplication, or real coverage.

## Run it

```bash
bismark prepare genome --bowtie2                          # CT + GA converted indices
bismark align --genome genome --single_end reads.fq        # directional bisulfite alignment
bismark extract -s --bedGraph --comprehensive reads_bismark_bt2.bam
```

One task: build the reference and reads, prepare, align, extract, then compare the per-site counts to the planted ones.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 1 kb reference with 3 planted CpGs | your genome | every other `CG` is deliberately removed from the fixture, so the tool has exactly three sites it can possibly report — that is what makes "no spurious calls" assertable. |
| reads converted in-code | your WGBS FASTQs (after `trim_galore`) | real libraries need adapter/quality trimming first, and RRBS needs `--rrbs`; neither changes the shape below. |
| directional (default) | `--non_directional` | get this wrong and reads align poorly for reasons that look like bad data. |
| single-end | `-1`/`-2` paired-end | paired-end also enables `dedup`, which this fixture deliberately skips. |

**Leave the fixture:** 30 reads over three sites make every methylation count hand-checkable, which is what turns this into an exact assertion. **Scale it** to a real genome — and note that `prepare` on a mammalian reference is hours and tens of gigabytes, not seconds.

## Shape, size, cost

One task, `c8g.large` (2 vCPU / 4 GiB), TTL 15m, cap $0.05. Recorded window **1m02s** for the whole prepare → align → extract chain at this size. **These timings are not compute cost.**

<details>
<summary>As shipped: exact per-site counts, why the 0% site matters, a conservation identity, the Rust rewrite, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| reference CpGs | exactly 3 — every other `CG` removed | **3** |
| mapping | 100% unique, none ambiguous | **100.0%**, 0 non-unique |
| sites reported | exactly 3 — no spurious calls | **3** |
| per-site counts | methylated / unmethylated **equal the planted values** | **exact** |
| conservation | Σ calls == read count | **30 of 30** |

Recovered exactly:

```text
chrS  200  100%  10 meth /  0 unmeth      (planted 10/0)
chrS  400   50%   5 meth /  5 unmeth      (planted 5/5)
chrS  600    0%   0 meth / 10 unmeth      (planted 0/10)
```

**Why the 0% site is the most informative one.** In bisulfite sequencing an unmethylated cytosine is read as **T**. A tool that treated those T's as sequencing mismatches rather than as conversion events would either fail to align those reads or call the site methylated. Requiring `0 meth / 10 unmeth` asserts that the conversion is being interpreted, not tolerated — and it is the case a fixture with only methylated sites would never exercise. Planting 100%, 50% and 0% covers the full range rather than one convenient point.

**The conservation identity** works because each read covers exactly one CpG by construction, so total calls must equal the read count. That catches reads silently dropped between alignment and extraction — a gap neither the mapping report nor the per-site percentages would reveal on their own.

**Removing the incidental CpGs is what makes "no spurious calls" meaningful.** A random 1 kb sequence contains many `CG` dinucleotides; the fixture strips them all and plants three. Without that, "3 sites reported" would be a coincidence rather than a claim.

### This is Bismark 3.x — the Rust rewrite, not the Perl suite

`bismark --version` reports *"Bismark Rust suite"*. It is **one binary with subcommands** (`prepare`, `align`, `extract`, `dedup`, `bedgraph`, …), with the classic script names kept as aliases. Two practical consequences:

- Older guides' flags may not exist; read `bismark <subcommand> --help` rather than assuming the Perl interface.
- The image ships `bowtie2` but **no samtools**, and does not need it — the Rust suite writes BAM natively. Tools that expect to shell out to samtools would not survive here.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| image | `quay.io/aarchbio/bismark@sha256:1bdd5895…` (3.1.0, bundling bowtie2) |
| input | none — reference, planted methylation state and bisulfite-converted reads are generated in-task by awk from `srand(5)` |

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before `bismark` is callable.

### Run + verify

```sh
make run RECIPE=bismark
make ls  RECIPE=bismark
```

Assertions are `test` calls inside the task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `calls_exact yes`, `sites_reported 3` and `total_calls 30 of 30`.

</details>
