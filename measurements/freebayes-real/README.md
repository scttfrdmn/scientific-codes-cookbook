# Bayesian variant calling is the catalog's biggest generational gain — and the only even one

> **freebayes on whole chr20 at 30× is 2.65× faster on Graviton5 than Graviton2**, the largest gain
> measured here. More interesting than the headline: the per-step gains are **1.59×, 1.32×, 1.27×**
> — no weak step. Every other long ladder in this catalog stalls at Graviton3→4, and this one does
> not, which breaks a generalisation that had been forming.

freebayes 1.3.10, NA12878 chr20 at 30× (`NA12878.chr20.30x.bam`) against chr20 of GRCh38,
4 vCPU on every rung, same image digest, same staged input objects.

| generation | instance | calling | billed | $/hr | **compute $** | billed $ | variants |
|---|---|---|---|---|---|---|---|
| Graviton2 | `c6g.xlarge` | 1349 s | 1456 s | 0.1360 | **0.0510** | 0.0550 | 385,371 |
| Graviton3 | `c7g.xlarge` | 851 s | 926 s | 0.1450 | **0.0343** | 0.0373 | 385,371 |
| Graviton4 | `c8g.xlarge` | 644 s | 704 s | 0.1595 | **0.0285** | 0.0312 | 385,371 |
| **Graviton5** | `c9g.xlarge` | **509 s** | 559 s | 0.1739 | **0.0246** | 0.0270 | 385,371 |

`$/hr` rises **27.9%** across the ladder while the same call gets **52% cheaper**. `billed/compute`
is **1.08–1.10** on every rung — the tightest ratio in this directory, because 8–22 minutes of work
makes the ~100 s of provisioning almost invisible. Both columns agree on direction and magnitude,
so neither needs a caveat.

## The even ladder is the finding

| code | Gv2→Gv5 | per-step | Gv3→Gv4 |
|---|---|---|---|
| **freebayes** (Bayesian caller) | **2.65×** | 1.59 / **1.32** / 1.27 | **1.32×** |
| [ambertools](../ambertools-real/README.md) (serial MD) | 2.50× | 1.52 / **1.16** / 1.42 | 1.16× |
| bedtools `genomecov` | 2.33× | 1.69 / 1.16 / 1.19 | 1.16× |
| HMMER | 1.90× | 1.43 / **1.06** / 1.26 | 1.06× |
| picard | 1.93× | — | — |
| SIESTA | 1.86× | — | — |

A pattern had been accumulating that Graviton3→4 is nearly free for likelihood and matrix codes —
HMMER at 1.06× is actually *dearer per result*, and ambertools and bedtools both sit at 1.16×.
**freebayes refutes the generalisation on its own terms:** it is a Bayesian haplotype caller, i.e.
squarely a likelihood code, and it gains **1.32×** at exactly that step. So the weak Gv3→Gv4 step is
a property of particular inner loops, not of likelihood methods as a class, and not of the step.

Together with ambertools this also moves the catalog's spread again. The gains now run **1.86×–2.65×**
— a 1.43× range — so quoting any single generational multiplier is wrong by up to 40% depending on
which code you meant.

## The free cross-generation identity

**385,371 variants on all four chips**, plus the sample name and zero off-chr20 records. freebayes
is deterministic given the same input and thread count, so each rung is a correctness check on the
other three and n = 1 per generation is defensible.

Worth contrasting with [ambertools](../ambertools-real/README.md), where the same ladder gave
**three different trajectories** grouped by the OpenBLAS kernel the host selects. The difference is
not that one code is better behaved: freebayes' variant set is a decision — a fixed point of its
model — while an MD trajectory is a path, and freebayes doesn't route its inner loop through a
`DYNAMIC_ARCH` BLAS. Identical output across chips is evidence the quantity is a fixed point, not
evidence that floating point is portable.

## Reproducing it

```sh
make stage RECIPE=freebayes                    # once; the chr20 BAM + reference
B=$(make -s print-bucket)
for f in c6g c7g c9g; do
  sed "s|\${COOKBOOK_BUCKET}|$B|g" measurements/freebayes-real/gen-$f.task.json > /tmp/fb-$f.json
  spawn task run --spec /tmp/fb-$f.json --wait   # serially — see below
done
```

Graviton4 needs no run: the recipe's own verified calling task **is** that rung, same spec and
digest ([`cookbook-freebayes-chr20`](../../recipes/freebayes/README.md), 644 s / 704 s billed).

**Launch the rungs serially**, per [truffle#175](https://github.com/spore-host/truffle/issues/175)
— the static price fallback has no Graviton coverage, so a throttled Price List call refuses a
Graviton launch carrying a cost limit, and concurrency is what triggers the throttle. TTL is per
rung (45m/35m/25m, caps $0.14/$0.12/$0.10), scaled from the measured Graviton4 wall by the worst
slowdown this catalog had seen; the slowest rung used 1456 s of its 2700 s.

Raw smoke-check output per rung and the ladder as TSV are in [`results/`](results/).
