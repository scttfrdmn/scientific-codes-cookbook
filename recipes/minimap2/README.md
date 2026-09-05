# minimap2 ← bwa — short-read alignment, cross-validated against bwa on identical reads

One task. minimap2 aligns the **same 400,000 read pairs bwa already aligned** (recipe
`bwa-samtools`) to the **same chr20**, and the smoke check confirms that where both
aligners are confident, they place reads at the same locus — a two-aligner cross-check
on identical bytes, not either tool's self-report.

> **What this recipe does and does not cover.** It aligns a 400k-pair slice of HG00096
> (100 bp reads) to GRCh38 chr20 with minimap2's short-read preset and cross-checks the
> confident placements against bwa — enough to prove minimap2's `-ax sr` path works on
> Graviton4 and agrees with an independent aligner. Not a benchmark; no full-genome
> reference, structural-variant, or long-read work.

## The identity: confident concordance, not "same position for every read"

minimap2 and bwa are independent seed-and-extend aligners. Run on identical reads and
reference they agree overwhelmingly **where each is confident** — but *not* on every
mapped read, and asserting that would be the arbitrary-tie-break trap: most of these
whole-genome reads do not belong on chr20 at all, and `mem`/`-ax sr` both still report a
low-quality single-chromosome placement for many of them, landing in repeats where the
two tools break multi-mapping ties differently. So the claim is scoped to what is
actually true:

- **Confident concordance (the cross-code identity).** Of the reads that **both** aligners
  place with MAPQ ≥ 30, the fraction put at the same locus (within 5 bp, absorbing
  soft-clip/indel-representation differences) is **0.9921** — 19,892 of 20,051. Two
  unrelated aligners landing confident reads on the same base is the RAxML-NG/IQ-TREE
  cross-code move applied to alignment. The tolerance is set by the method (soft-clip and
  indel-shift differences, not locus disagreement), not by the observed value; a broken
  index or wrong reference collapses it far below 0.98.
- **minimap2 mapped count is *reported, not asserted equal to bwa's*.** minimap2 maps
  169,183 primaries and bwa 233,036 — a legitimate sensitivity difference between the two
  presets, most of it low-MAPQ spurious placement. Equating them would be asserting a
  coincidence; the concordance above is the real claim.

The reads are the exact staged bytes bwa aligned (`inputs/bwa-samtools/`), and the
reference is the same chr20 — the cross-check only means something on identical input, so
nothing is re-staged (per the cross-validation rule).

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a` |
| | tag `2.31--he84ed4f_0`, minimap2 2.31-r1302, cosign-verified (aarchbio `publish.yml`), `linux/arm64` |
| reads | `inputs/bwa-samtools/HG00096_chr20smoke_{1,2}.fq.gz` — 400k pairs, 100 bp, sha256-pinned |
| reference | `inputs/bwa-samtools/chr20.fa` — GRCh38 chr20 (64,444,167 bp), sha256-pinned |
| cross-check | `runs/bwa-samtools/r1/aln.sam` — bwa's alignment of the same reads |

**Data tier: reused.** Nothing new is staged; the reads and reference are `bwa-samtools`'s
pinned inputs and the cross-check reference is its output. No `stage-inputs.sh`.

## Smoke check

Measured in this image, before any launch.

| observable | assertion | observed |
|---|---|---|
| bwa reference primary | exactly 800000 (right cross-check file) | 800000 |
| minimap2 primary | exactly 800000 (400k pairs × 2) | 800000 |
| minimap2 mapped | 100000..250000 (sensitivity, not equal to bwa) | 169183 |
| minimap2 MAPQ≥30 | 10000..40000 | 21990 |
| both confident | > 10000 (concordance denominator) | 20051 |
| **confident concordance** | **≥ 0.98** (same locus, both MAPQ≥30) | **0.9921** |

The concordance is deterministic (both aligners are deterministic on fixed input; the
metric is computed per-read, order-independent), so 0.98 is a floor with method-margin,
not a band on noise. SAM is parsed with `awk` arithmetic flag tests — the image has no
samtools or python.

## Resources, and what the timings mean

4 vCPU / 8 GiB, `c8g` (resolves to `c8g.xlarge`), TTL 5m, cap $0.02. The
index + `-ax sr` alignment of 400k pairs is **~12 s** locally.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
minimap2 image are the whole task. The recorded run's window was **58s** (05:28:28 →
05:29:26 UTC), confident concordance vs bwa 0.9921. TTL was **retightened from that first
real run**: 10m → **5m**, `cost_limit` $0.03 → $0.02; the recorded run used the original 10m.
A loose TTL is a larger blast radius, not caution. Disk is trivial
(chr20 + reads + both SAMs ≈ 0.6 GiB).

## Running it

No `stage-inputs.sh` — inputs are `bwa-samtools`'s staged bytes, so run that recipe first
(or confirm its objects exist).

```sh
spawn task run --spec recipes/minimap2/01-align.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/minimap2/r1/
```

The smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect two objects (`mm.sam`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
