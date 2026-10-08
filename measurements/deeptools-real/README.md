# deepTools bamCoverage — an exact conservation identity against samtools, and why it took three tries

> **`bamCoverage --binSize 1 --normalizeUsing None` reproduces samtools' aligned-base count exactly
> — 2,998,490,520 on both sides, zero difference, no tolerance.** Getting there required two
> accounting corrections, and each near-miss had an exact cause rather than a tolerance to widen.

deepTools 4.0.0 `bamCoverage` over the full ENCODE CTCF ChIP-seq alignment `ENCFF933NSJ` (1.92 GiB,
39,455,565 mapped reads, 76 bp), checked against `samtools stats` on the same BAM.

## The identity

| | |
|---|---|
| samtools `bases mapped (cigar)` | 2,998,622,940 |
| − inserted bases (from `ID` lines) | 132,420 |
| **= M bases (reference-aligned)** | **2,998,490,520** |
| **bamCoverage integral, binSize 1** | **2,998,490,520** |
| **difference** | **0** |

Both numbers count the same physical thing — reference positions covered by aligned read bases — by
two completely independent routes, so they must agree to the base. They do. That is a conservation
identity in the sense this project means it: not a band, not a correlation, and it cannot be
satisfied by an implementation that miscounts.

## The two corrections, because the near-misses are the content

### 1. Bin width inflates the integral by `(L-1+W)/L`

The first attempt asserted the integral equalled samtools' mapped bases at `--binSize 50`, within
5%. Measured **1.644889** — refused.

**At bin width W, `bamCoverage` credits a read to every bin it touches**, so each read is spread
over roughly `L-1+W` reference positions instead of `L`:

| | |
|---|---|
| predicted ratio `(L-1+W)/L` = (76−1+50)/76 | 1.644737 |
| observed | **1.644889** |
| agreement | **0.0093%** |

Neither tool is wrong — the metric was comparing a *binned* quantity against an *unbinned* one. The
fix is not a wider tolerance but `W = 1`, where `L-1+W = L` and the identity holds by construction.

This is still **reported rather than asserted**, with only a loose sanity band, because indels make
each read's reference span differ from its length, so `(L-1+W)/L` is an account rather than an exact
law.

### 2. `bases mapped (cigar)` counts M+I; coverage counts M

At `binSize 1` the integral came out **4.4e-05 short** — 132,420 bases in 3.0 Gbp. A tolerance of
1e-4 would have passed it and taught nothing.

samtools' `bases mapped (cigar)` counts **M and I**, because an insertion consumes *query* bases.
Reference coverage counts **M only** — an insertion occupies zero reference positions. The `ID`
lines of the already-staged `samstats.txt` sum to exactly **132,420** inserted bases, and the gap
was exactly 132,420. Confirmed to the base, from data already on hand, at no cost.

So the correction is **accounting, not tolerance**. (Deletions, 527,460 bases, do not enter: they add
reference span but no read bases, and coverage counts the latter.)

## This leg has no generation sweep, deliberately

`bamCoverage` took **27 s at binSize 1 and 18 s at binSize 50** on 16 cores, inside a ~134 s task
window. Compute is roughly 20% of the billed time and the rest is boot, image pull and staging.

**A four-generation table over an 18–27 s workload would be measuring boot, not the tool** — the
per-generation differences the other legs resolve (macs2 2.40×, bowtie2 2.06×) are smaller than the
run-to-run variance of a window that short. Rather than publish a table that looks like a
measurement and isn't, the honest result is: *this step is boot-dominated and the instance
generation is not a lever for it.* That is directly useful to a reader deciding where to spend
effort.

For comparison, the same lesson in the other direction: the bowtie2 index build in this batch took
985 s on one run and 1352 s on the next — **37% variance on the same instance type, identical work**.
Short windows are not measurements.

## Shape and cost

Two tasks, because deepTools reads a BAM but cannot create or index one — the same split the shipped
recipe uses, at full scale. ENCODE publishes no `.bai`, so it must be built.

| task | instance | measured |
|---|---|---|
| `01-index-and-stats` | `m8g.xlarge` | `samtools index` + `stats`, 114 s window |
| `03-coverage-identity` | `m8g.4xlarge` | 27 s + 18 s of bamCoverage, 134 s window |

`tmpfs_used_mib` **2,466** — comfortably inside anything. Output bigWigs are 332 MB (binSize 1) and
194 MB (binSize 50); writing bigWig rather than bedgraph is what makes `binSize 1` viable at all, a
3.1-billion-row bedgraph being the alternative.

The integral is read from the bigWig `totalSummary.sumData`, which is the sum of value × width over
covered bases — the integral directly, O(1), and bin-width agnostic.

## Pins

| | |
|---|---|
| BAM | `ENCFF933NSJ.bam`, 2,057,454,374 B, ENCODE md5 `48f06f46ac59b93e6ae3110de9730a3e` |
| images | `quay.io/aarchbio/deeptools@sha256:91c028a2…` (4.0.0), `quay.io/aarchbio/samtools@sha256:11917396…` |

ENCODE publishes an md5 per file, so the bytes are checkable at source; the gate runs inside the
task next to them.

## Caveats

**One BAM, one mark.** CTCF is a sharp, punctate factor. A broad mark or an RNA alignment would
change the coverage profile — though not the identity, which is arithmetic rather than biology.

**`deeptools.__version__` is absent in this build** and reports `unknown` through a `getattr`
fallback; the version in the table comes from `bamCoverage --version`. The fallback is deliberate —
a line whose only job is to describe the run must not be able to fail it.

**No `avg_cores` or peak-RSS instrument on the final run.** The sampler was dropped when the task was
rewritten around the identity, and was not restored because the correctness result, not the resource
profile, is what this leg contributes. The earlier binSize-50 run measured `avg_cores 3.42` of 8 and
`peak_rss 1115 MiB`, which is the only parallel-efficiency figure here and suggests `bamCoverage`
scales poorly — unverified at 16 cores, and worth a look before anyone sizes a big box for it.
