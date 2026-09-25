# bwa mem on a real workload — Graviton generations and the knee

> **Every Graviton generation is faster *and* cheaper per result than the one before it — and
> the cost knee is at 16 cores, not 64, once you pay for the instance rather than the CPU
> seconds.** Measured on a full 1000 Genomes sequencing run against the whole of GRCh38.

The workload is deliberately not a toy: **24,148,993 read pairs** (4.83 Gbp, 100 bp) aligned
to the complete `GRCh38_full_analysis_set_plus_decoy_hla` index. 99.91% of records map, every
run. At 16 cores that is 10–18 minutes of real alignment, so the numbers below are dominated
by the science rather than by boot.

**The scripts are meant to be run.** Every launch is `spawn task run` with a TTL,
`on_complete: terminate` and a `cost_limit`.

## Run it

```sh
export AWS_PROFILE=aws

# generation sweep: 16 threads everywhere, same image, same bytes
for f in c6g c7g c8g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" gen-$f.task.json > /tmp/g.json
  spawn task run --spec /tmp/g.json --wait
done

# knee sweep: one generation, more cores
for c in 32 48 64; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" knee-$c.task.json > /tmp/k.json
  spawn task run --spec /tmp/k.json --wait
done
```

Inputs are the **published** bwa index and reads on the `1000genomes` RODA bucket, cached
once into your own bucket so every run stages same-region:

```sh
SRC=s3://1000genomes
DST=s3://$COOKBOOK_BUCKET/inputs/bwa-real
REF=technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa
for e in amb ann bwt pac sa fai; do aws s3 cp "$SRC/$REF.$e" "$DST/$(basename $REF).$e"; done
for r in 1 2; do
  aws s3 cp "$SRC/phase3/data/HG00096/sequence_read/SRR062634_${r}.filt.fastq.gz" "$DST/"
done
```

`bwa mem` reads `<prefix>.amb/.ann/.bwt/.pac/.sa` only — **the 3.0 GiB `.fa` is not needed**,
which cuts staging from 12 GiB to **8.9 GiB**. No index build: RODA publishes one.

## 1. Generations — newer is faster *and* cheaper, monotonically

16 threads on a `4xlarge` of each generation. Same container digest, same input bytes, so
this is a [clean within-image comparison](../../patterns/cost-per-result.md).

| generation | instance | wall | reads/s | avg cores | $/hr | **compute $/result** | **billed $/result** |
|---|---|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | 1086 s | 44,560 | 15.21 | 0.5440 | 0.1641 | 0.1841 |
| Graviton3 | `c7g.4xlarge` | 890 s | 54,373 | 15.26 | 0.5800 | 0.1434 | 0.1621 |
| Graviton4 | `c8g.4xlarge` | 757 s | 63,926 | 15.21 | 0.6381 | 0.1342 | 0.1508 |
| **Graviton5** | `c9g.4xlarge` | **590 s** | **82,021** | 15.18 | 0.6955 | **0.1140** | **0.1320** |

Across the range the rate card rises **+27.9%** and the wall falls **−45.7%**, so the result
gets **30.5% cheaper** (28.3% billed). Graviton5 is **1.84×** faster than Graviton2 on the
same bytes. There is no generation where paying less per hour got a cheaper result — the
pricier box wins at every step, which is the same conclusion `cost-per-result.md` reached on
the assemblers, now reproduced on a completely different kind of code.

Peak RSS was **8.70–8.71 GiB on all four** — identical to three digits, so memory footprint
is a property of the index and the data, not the chip. A 32 GiB box is right for 16 threads.

## 2. The knee — and why compute-only would mislead you

More cores on one generation (`c8g`, same family so the rate scales with size):

| cores | instance | wall | reads/s | avg cores | eff. | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|---|---|
| 16 | `c8g.4xlarge` | 757 s | 63,926 | 15.21 | 95% | 8.70 GiB | 0.6381 | 0.1342 | **0.1508** |
| 32 | `c8g.8xlarge` | 387 s | 125,044 | 28.80 | 90% | 12.02 GiB | 1.2762 | 0.1372 | 0.1705 |
| 48 | `c8g.12xlarge` | 259 s | 186,842 | 40.43 | 84% | 15.41 GiB | 1.9142 | 0.1377 | 0.1888 |
| 64 | `c8g.16xlarge` | 200 s | 241,961 | 51.09 | 80% | 18.65 GiB | 2.5523 | 0.1418 | 0.2106 |

**bwa scales unusually well** — 3.79× faster on 4× the cores, and **compute-only cost rises
only 5.7%** across that range. On CPU seconds alone the honest advice would be "take 64
cores, the speed is nearly free."

**The bill says otherwise.** Billed cost rises **40%** from 16 to 64 cores, because the
fixed overhead — boot, image pull, and staging 8.9 GiB — is **93–97 seconds regardless of
instance size**, and on the big box every one of those seconds costs 4× more. Overhead is
11% of the 16-core run and **33% of the 64-core run**.

So for this workload:

- **Paying per instance (i.e. reality): the cost knee is 16 cores.** Cheapest result, and
  the overhead share is smallest.
- **64 cores is a wall-clock purchase**: 3.79× faster for 40% more money. Buy it when the
  deadline is worth 40%, not because "the cores are nearly free."
- **A cohort should be sized at 16 and fanned out** ([job arrays](../../patterns/job-arrays.md)),
  which amortises nothing about boot but keeps every instance on the cheap side of the curve.

This is the [billed-vs-compute distinction](../../patterns/cost-per-result.md) doing real
work: the two measures rank the *same* runs differently, and only one of them is what you pay.

**Memory grows with threads** — 8.70 GiB at 16 to 18.65 GiB at 64, about 0.21 GiB per extra
thread on top of the shared index. Size memory for the thread count you will actually use;
the 16-thread figure will OOM a 64-thread run on a 16 GiB box.

## 3. Shape: a fourth kind of scaling

[`patterns/sizing.md`](../../patterns/sizing.md) sorts codes into three shapes — *the answer
moves* (flye), *the cost climbs* (GROMACS), *it hits a wall* (GPAW). bwa is a fourth:
**the answer is stable, the speedup is near-linear, and compute cost is flat — so the only
thing that bends the curve is the fixed overhead.** For codes of this shape the sizing
question stops being about the chip and becomes about the data path, which is why
[copy, mount, or share?](../../patterns/data-movement.md) is the page that matters next: the
94 seconds of staging is the entire reason 64 cores loses.

## Caveats

n = 1 per cell. Generation rows share one image digest, one input, one thread count, so they
are comparable to each other; the knee rows share one generation and family. `avg_cores` is
cgroup `cpu.stat usage_usec` over wall, and no family here has SMT, so it is
physical-core-equivalent throughout. Rates are us-west-2 on-demand at time of measurement.
The 16-core row is reused in both tables — it is the same run.

A first attempt died twice with **exit 141** before producing anything: `awk … exit` and
`| head` both stop reading early, SIGPIPE the writer, and under `set -o pipefail` kill the
task after all the work is done. Neither shows up as a bug in the science. The scripts here
read every pipeline to EOF.
