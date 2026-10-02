# Interval arithmetic gains as much from a new chip as plane-wave DFT does

> **bedtools `genomecov` over a whole-genome BAM is 2.33× faster on Graviton5 than Graviton2** — tying
> GPAW, and beating every genomics code measured here. Three quarters of the gain arrives in one step,
> at Graviton3.

bedtools 2.31.1, `genomecov -ibam` over bwa's sorted BAM (48,817,006 records, 4.5 GB, 3,366 contigs),
8 vCPU on every rung, same image digest, same input object.

| generation | instance | `genomecov` | billed | $/hr | **compute $** | billed $ |
|---|---|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 286 s | 420 s | 0.2720 | **0.0216** | 0.0317 |
| Graviton3 | `c7g.2xlarge` | 169 s | 290 s | 0.2900 | **0.0136** | 0.0234 |
| Graviton4 | `c8g.2xlarge` | 146 s | 284 s | 0.3190 | **0.0129** | 0.0252 |
| **Graviton5** | `c9g.2xlarge` | **123 s** | 229 s | 0.3478 | **0.0119** | 0.0221 |

Per-step: **1.69×, 1.16×, 1.19×.** The shape is lopsided in a way none of the other ladders here are
— Graviton2→3 alone is bigger than the other two steps combined.

**The billed column is not the comparison.** At ~2–5 minutes of work, boot and image pull are most of
the instance's life, so billed puts Graviton3 ($0.0234) ahead of Graviton4 ($0.0252) while
compute-only has them 5% apart the other way. That is the regime
[cost-per-result](../../patterns/cost-per-result.md) calls boot-dominated; read compute, or amortise
boot over more samples per box.

## Two conservation identities, one of them cross-tool

| observable | assertion | observed |
|---|---|---|
| **genome bases** | **== the reference `.fai` total** | **3,217,346,917** |
| **per-contig bases** | **== the genome aggregate** | **3,217,346,917** |
| covered ≥1× | recorded | 2,164,818,308 (67.2858%) |
| mean depth | recorded | 1.4875× |
| max depth | recorded | 17,991 |

Every base of all 3,366 contigs is counted at exactly one depth, so the per-contig rows must sum to
the `genome` rows — the same identity the recipe's hand-checked 400 bp fixture asserts, scaled by
8 million. The second check is the one worth copying: the total is compared against the sum of contig
lengths in `GRCh38…fa.fai`, **a file bedtools never reads**, so an independent authority confirms the
aggregate rather than bedtools confirming itself. Cost: staging a 160 KB index.

All five numbers were identical on all four generations, to the digit, including the tmpfs
high-water mark (4,359–4,360 MB). Interval arithmetic is deterministic, so that is an exact
assertion, and it is what makes n = 1 per rung defensible.

## A number another recipe was leaning on

[picard](../../recipes/picard/README.md) explains its 0.87% duplicate rate by this library being
"~1.5× genome-wide" — at that depth two reads rarely start at the same position by chance. bedtools
measures **1.4875×** here, a different tool on a different pass over the same BAM. Neither recipe
asserts it (both are library properties, not tool properties), but the explanation is no longer
resting on a figure nobody had checked.

67.29% of the reference covered at ≥1× is the expected shape for a ~1.5× library: at Poisson λ=1.4875
the uncovered fraction would be e^−1.4875 ≈ 22.6%, and the observed 32.7% is higher because coverage
is not Poisson — repeats, unmappable regions and the decoy contigs take their share. Max depth 17,991
is the other end of that: centromeric and repeat pileups.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make stage RECIPE=bwa-samtools
make run   RECIPE=bedtools                   # the Graviton4 rung, as shipped
spawn task run --spec measurements/bedtools-real/gen-c6g.task.json --wait
spawn task run --spec measurements/bedtools-real/gen-c7g.task.json --wait
spawn task run --spec measurements/bedtools-real/gen-c9g.task.json --wait
```

`make run` substitutes `${COOKBOOK_BUCKET}`; `spawn task run` does **not** — resolve it first or
stage-in fails with `Invalid bucket name "${COOKBOOK_BUCKET}"`.

## Caveats

n = 1 per generation, defensible only because the result is identical across all four. The per-step
speedups are *not* flat here, so unlike [picard](../picard-real/README.md) the trend itself is a
single observation — the 1.69× Graviton2→3 step in particular would be worth repeating before anyone
builds a purchasing argument on it.

Why that step is large is not established. `genomecov -ibam` is BGZF decompression plus a sweep over
interval starts and ends, so a memory-bandwidth or decompression-throughput measurement would be
needed to attribute it; the wall clock alone cannot.

One instance size and one BAM. A deeper library would raise the depth histogram's tail but not the
genome total, so the identities hold at any depth — the timings would not.
