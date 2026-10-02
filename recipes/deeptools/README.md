---
tool: deeptools
tool_version: "4.0.0"
images:
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/deeptools@sha256:91c028a26e85579dde0eadb0efd06c662bcdcb52956e48c941cdeea6c3016ca8
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# deepTools — a coverage track, checked base by base against a profile we built

`bamCoverage` turns aligned reads into a coverage track on Graviton4, verified against a coverage profile that was constructed rather than observed. For anyone doing ChIP-seq, ATAC-seq or any coverage-based analysis.

> **What this covers.** 10 reads of 100 bp placed at chosen positions — a stack of five, a deliberate overlap, and a singleton — then `bamCoverage` at single-base resolution, unnormalised. Not bigWig scaling, normalisation methods, `multiBamSummary`, or real signal.

## Run it

```bash
for s in $(make -s spec RECIPE=deeptools); do spawn task run --spec "$s" --wait; done
samtools sort -o reads.bam reads.sam && samtools index reads.bam
bamCoverage -b reads.bam -o cov.bedgraph --outFileFormat bedgraph \
            --binSize 1 --normalizeUsing None
```

Two tasks: samtools builds the indexed BAM (deepTools reads BAM but cannot create one), then `bamCoverage` produces the track and it is compared to the expected profile byte for byte.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| 10 synthetic reads at chosen positions | your BAM (e.g. from [bwa-samtools](../bwa-samtools/README.md)) | the constructed profile is the answer key — real signal has none, only internal consistency. |
| `--binSize 1 --normalizeUsing None` | your bin size + `RPKM`/`CPM`/`BPM` | **normalisation breaks the conservation check below**, on purpose: once you scale, the coverage integral no longer equals aligned bases. Verify unnormalised first, then normalise. |
| bedgraph output | bigWig (deepTools' default) | bedgraph is text and therefore diffable, which is why the recipe asserts on it; bigWig is what you want downstream. |

**Leave the fixture:** ten reads make every base of the expected track hand-checkable, which is what lets this assert an exact profile instead of a summary statistic. **Scale it** to a real BAM once you trust the tool's arithmetic.

## Shape, size, cost

Two tasks on `c8g.large` (2 vCPU / 4 GiB), TTL 12m each, caps $0.05 each. The coverage step runs in seconds; the recorded window is dominated by image pull and Python startup. **These timings are not compute cost.**

<details>
<summary>As shipped: a conservation identity, the whole track asserted exactly, what the overlap proves, pins</summary>

### Four exact checks, no bands

| observable | assertion | observed |
|---|---|---|
| coverage integral | `Σ (end−start) × depth` == aligned bases | **1000** = 10 × 100 bp |
| max depth | == the planted stack | **5** |
| nonzero intervals | == 5 | **5** |
| the whole profile | **byte-identical** to the constructed track | **yes** |

**The conservation identity is the load-bearing one.** Coverage is a redistribution of aligned bases: every base a read covers must appear exactly once in the track, so the integral has to equal reads × read-length. That single number catches off-by-one interval boundaries, dropped reads, double counting, and accidental normalisation — none of which a spot check on one interval would notice. It is exact because coverage here is integer, which is also why `--normalizeUsing None` is not an incidental flag but the thing that makes the check possible.

**The whole track is compared, not sampled.** The expected bedgraph is written out before deepTools runs and then `cmp`-ed against the output:

```text
999  1099  5     <- five stacked reads (bedgraph is 0-based; SAM pos 1000)
1999 2049  2
2049 2099  4     <- the overlap
2099 2149  2
4999 5099  1
```

**What the overlap proves.** Two reads at 2000 and two at 2050 produce depth 2, then **4**, then 2. If deepTools took a maximum, or let the last read win, that middle interval would read 2. Requiring 4 asserts that overlapping reads **sum** — the one semantic in a coverage tool most worth pinning down, and invisible to any check that only looks at totals.

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| samtools | `quay.io/aarchbio/samtools@sha256:11917396…` — the same pin the [bwa-samtools](../bwa-samtools/README.md) recipe uses |
| deepTools | `quay.io/aarchbio/deeptools@sha256:91c028a2…` (4.0.0) |
| input | none — the SAM and the expected profile are generated in-task by awk |

**Why two images:** deepTools 4.0.0 ships `bamCoverage` but **no samtools**, so it can read a BAM and not make one. That is the [one tool per image](../../patterns/execution-shapes.md) model doing its job rather than an inconvenience — the BAM is built in the samtools image and handed over through S3.

Also: spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before either tool is callable.

### Run + verify

```sh
make run RECIPE=deeptools
make ls  RECIPE=deeptools
```

Assertions are `test` calls inside the second task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `coverage_integral 1000`, `profile_identical yes`, and the overlap line reading depth 4.

</details>
