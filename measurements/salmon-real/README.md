# salmon on a real workload — Graviton generations

> **Every generation is faster and cheaper per result, and the newest also stages fastest.**
> But 40% of the bill is boot and staging, not salmon — the opposite balance from
> [bwa](../bwa-real/README.md).

Workload: the **complete** `ERR188026` Geuvadis run (15,800,127 reads) against **all 453,553**
Ensembl 116 transcripts. Not a slice, not a subset annotation.

## Run it

```sh
export AWS_PROFILE=aws
for f in c6g c7g c8g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" 02-quant-$f.task.json > /tmp/q.json
  spawn task run --spec /tmp/q.json --wait
done
```

The index is built once by `recipes/salmon/01-index.task.json` (50 s, 1.68 GB tar) and shared
by every run in the sweep — which is also how a real cohort should use it.

## Results

16 threads, one `4xlarge` per generation, same image digest, same input bytes:

| generation | instance | quant wall | $/hr | compute $/result | billed $/result | overhead |
|---|---|---|---|---|---|---|
| Graviton2 | `c6g.4xlarge` | 116 s | 0.5440 | 0.0175 | 0.0287 | 74 s |
| Graviton3 | `c7g.4xlarge` | 83 s | 0.5800 | 0.0134 | 0.0259 | 78 s |
| Graviton4 | `c8g.4xlarge` | 74 s | 0.6381 | 0.0131 | 0.0222 | 51 s |
| **Graviton5** | `c9g.4xlarge` | **57 s** | 0.6955 | **0.0110** | **0.0193** | **43 s** |

- **2.04× faster** Graviton2 → Graviton5; **37% cheaper** compute, **33% cheaper** billed.
- **Overhead shrinks with generation too** (74 → 43 s) for the same 3.7 GB of staged input —
  newer instances have more network. A generation step buys compute *and* bandwidth.
- **Peak RSS 6.46–6.59 GiB** on all four: footprint is the index, not the chip.
- `sum(TPM)` was **exactly 1000000.00** and `sum(NumReads)` **14,913,565** on every
  generation — the numerics are generation-independent, which is what makes the timings
  comparable at all.

## Why no knee sweep

At 16 threads quant finishes in about a minute against 43–78 s of fixed overhead. There is
nothing for more cores to buy — the [knee](../../patterns/sizing.md) is not the question for
this code; [the data path](../../patterns/data-movement.md) is.

## Caveats

n = 1 per cell. The first attempt at this sweep failed on all four with **exit 127**: the
salmon image has neither `python3` nor `jq`, and the script parsed `meta_info.json` with
python. `quant.sf` had already staged, so the science was recoverable from the output and only
the timings were lost. Check an image for the interpreters you assume before you rely on them.
