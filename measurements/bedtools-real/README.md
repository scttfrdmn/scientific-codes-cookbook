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

## The Graviton4 rung on Intel and AMD

Same spec (`gen-c8g.task.json`, the recipe's genomecov task as a rung), resolved with
`scripts/spec_arch.py bedtools x86_64|amd`: the recipe's pinned biocontainers bedtools 2.31.1,
8 vCPU, and **the same BAM object** the Graviton rungs read (`runs/bwa-samtools/r1/`, not the x86
re-alignment), so the input bytes are identical on all three chips.

| chip | instance | threads/core | `genomecov` | billed | $/hr | **compute $** | billed $ |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 1 | 146 s | 284 s | 0.3190 | **0.0129** | 0.0252 |
| Xeon 6975P-C | `c8i.2xlarge` | 2 | 130 s | 226 s | 0.3748 | **0.0135** | 0.0235 |
| **EPYC 9R45** | `c8a.2xlarge` | 1 | **101 s** | 221 s | 0.4311 | **0.0121** | 0.0265 |

- **AMD is the fastest chip measured here, Graviton5 included** (101 s against c9g's 123 s), and
  the cheapest on compute: 6% below Graviton4, 10% below Intel. Intel is 11% faster than Graviton4
  and 5% dearer on compute.
- **Billed ranks them the other way round**, for the boot-dominated reason above: 2–3 minutes of
  each bill is boot and image pull, charged at each chip's hourly rate.
- **`genomecov-wgs.txt` is byte-identical on all three chips** (sha256 `4b4c4c45…`), and so is
  every number in the table below. Interval counting is integer work, so this is a fixed point, as
  expected; there is no path to report. The BusyBox awk in the x86 image computed the same sums.
- n = 1 per chip, as for the generations. `wall_s` is `genomecov` alone; billed is EC2 launch to
  termination.

These two ran under spawn 0.139.1, whose wrapper takes ~14.7 KB of EC2's 16 KiB user data
(spawn#1246), so each resolved spec's `command` was staged unchanged as a sha256-checked file and
run with `bash`. One guarded line wrote `/proc/cpuinfo`'s model, siblings and cores to
`results/hwprobe-*.txt`. Neither changes what is computed or asserted.

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

All five numbers were identical on all four generations and on Intel and AMD, to the digit, including the tmpfs
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
# Intel and AMD. spec_arch moves the BAM input to runs-<arch>/bwa-samtools/; these legs edited it
# back to runs/bwa-samtools/ so all three chips read one object.
for a in x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py bedtools $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/bedtools-real/gen-c8g.task.json)" --wait
done
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
