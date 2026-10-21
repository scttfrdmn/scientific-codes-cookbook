# salmon on a real workload — Graviton generations, Intel and AMD

> **Every generation is faster and cheaper per result, and the newest also stages fastest.**
> But 40% of the bill is boot and staging, not salmon — the opposite balance from
> [bwa](../bwa-real/README.md). Same generation, AMD's `c8a` is fastest and ties Graviton4 per result;
> `quant.sf` is byte-identical on all three chips.

Workload: the **complete** `ERR188026` Geuvadis run (15,800,127 reads) against **all 453,553**
Ensembl 116 transcripts. Not a slice, not a subset annotation.

## Run it

```sh
export AWS_PROFILE=aws
for f in c6g c7g c8g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$COOKBOOK_BUCKET|g" 02-quant-$f.task.json > /tmp/q.json
  spawn task run --spec /tmp/q.json --wait
done
for a in x86_64 amd; do   # c8i / c8a, bioconda's amd64 salmon, each chip's own index
  spawn task run --spec "$(python3 ../../scripts/spec_arch.py salmon $a "$COOKBOOK_BUCKET" r1 02-quant-c8g.task.json)" --wait
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

## Same generation, three chips

The same spec on Graviton4's x86 siblings: `scripts/spec_arch.py salmon {x86_64,amd}` swaps in
bioconda's linux/amd64 salmon 2.7.0 and the family, and each chip reads the index its own salmon
built (`runs-{x86_64,amd}/salmon/r1/`, from the recipe's verifying runs). 16 threads, one
`4xlarge`, the same 3.7 GB of reads:

| chip | instance | cores × threads | quant wall | $/hr | compute $/result | billed $/result | overhead |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 16 × 1 | 74 s | 0.6381 | 0.0131 | 0.0222 | 51 s |
| Xeon 6975P-C | `c8i.4xlarge` | 8 × 2 | 90 s | 0.7497 | 0.0187 | 0.0277 | 43 s |
| **EPYC 9R45** | `c8a.4xlarge` | 16 × 1 | **55 s** | 0.8622 | **0.0132** | **0.0210** | **33 s** |

- **AMD is the fastest and, by a hair, the cheapest billed; Graviton4 ties it on compute.** AMD's
  1.35× speed over Graviton4 almost exactly offsets its 1.35× hourly price (0.0132 vs 0.0131 compute
  $/result). Intel's 16 vCPUs are 8 cores with SMT, and it is the slowest and dearest per result.
- **The overhead column is not comparable across rows.** Graviton4 ran under spawn 0.111 (Docker
  installed after stage-in); the x86 rows ran under 0.139.1, which overlaps them. Billed is the
  spawn wrapper's window (stage-in start to stage-out done), as for the generation rows; EC2's
  launch-to-terminate was 166 s (Intel) and 160 s (AMD).
- **n = 1 per cell, with a second sample free:** the recipe's own verifying runs of this same spec
  quantified in 85 s (Intel, 2026-10-12) and 57 s (AMD, 2026-10-19).
- **`quant.sf` is byte-identical on all three chips** (and on two AMD runs), though the arm64 and
  x86 indexes are different tars. `sum(TPM)` = 1000000.00 and `sum(NumReads)` = `num_mapped` =
  14,913,565 on each, the in-task checks that passed. Peak RSS 6.49 / 6.54 GiB.

spawn 0.139.1's wrapper leaves too little of EC2's 16 KiB user data for this spec's inline script
(spawn#1246), so the x86 legs staged the resolved command as a sha256-checked `task.sh`, byte for
byte what `spec_arch.py` emitted plus a non-failing CPU-topology print, and wrote to
`runs-{x86_64,amd}/salmon-real/r1/` so as not to overwrite the recipe's verified outputs
([`results/size-c8{i,a}-16t.txt`](results/); the spec's label inside reads `c8g-16t` on every
chip). Spend: $0.07.

## Why no knee sweep

At 16 threads quant finishes in about a minute against 43–78 s of fixed overhead. There is
nothing for more cores to buy — the [knee](../../patterns/sizing.md) is not the question for
this code; [the data path](../../patterns/data-movement.md) is.

## Caveats

n = 1 per cell. The first attempt at this sweep failed on all four with **exit 127**: the
salmon image has neither `python3` nor `jq`, and the script parsed `meta_info.json` with
python. `quant.sf` had already staged, so the science was recoverable from the output and only
the timings were lost. Check an image for the interpreters you assume before you rely on them.
