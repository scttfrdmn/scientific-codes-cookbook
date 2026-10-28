# Bayesian variant calling is the catalog's biggest generational gain — with no weak step

> **freebayes on whole chr20 at 36× is 2.65× faster on Graviton5 than Graviton2**, the largest gain
> measured here. More interesting than the headline: the per-step gains are **1.59×, 1.32×, 1.27×**
> — no weak step. Most ladders in this catalog are weakest at Graviton3→4, and this one is
> not, which breaks a generalisation that had been forming.

freebayes 1.3.10, NA12878 chr20 at 36× (`NA12878.chr20.30x.bam`, named for the published 30x CRAM it
is cut from: 36.3× mean depth, the recipe's BAM) against chr20 of GRCh38,
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

## Same generation, three chips: x86 calls faster

The Graviton4 rung against same-generation Intel and AMD, 4 vCPU each, from
[`chip.task.json`](chip.task.json) (the recipe's calling task plus a topology record and its own
output prefix; bioconda's `linux/amd64` build of 1.3.10 on both x86 chips):

| chip | instance | calling | billed | $/hr | **compute $** | billed $ | variants |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.xlarge` | 644 s | 704 s | 0.1595 | 0.0285 | 0.0312 | 385,371 |
| Intel Xeon 6975P-C | `c8i.xlarge` | 476 s | 543 s | 0.1874 | 0.0248 | 0.0283 | 385,371 |
| **AMD EPYC 9R45** | `c8a.xlarge` | **357 s** | 404 s | 0.2155 | **0.0214** | **0.0242** | 385,371 |

- **AMD is 1.80× faster than Graviton4 and 25% cheaper per call; Intel 1.35× and 13%.** Even
  Graviton5 (509 s) is slower than both. freebayes is single-threaded, so this is one core's speed:
  an Intel 4-vCPU box is 2 cores × 2 hyperthreads, Graviton4 and AMD 8a are 1 thread per core
  (`lscpu`), and the idle vCPUs do not matter. It is also two builds (aarch.bio's arm64, bioconda's
  x86), so the gap is chip and compiler together, not chip alone. n = 1 per cell.
- **What is identical: the variant count (all three), and every record above `QUAL≥30`** — the same
  113,841 sites, alleles and genotypes on all three, which is what the recipe's filter keeps.
- **What differs, ARM build against x86 build:** 4 of 385,371 records spell a homopolymer indel's
  alleles differently at the same position, and 3 more call a different genotype; all 7 have
  `QUAL` < 28. The other `QUAL` values agree except in the last printed digit (one record at 74.5)
  or below 1e-7. **Intel against AMD:** same sites, alleles, genotypes, INFO and sample columns on
  every record; `QUAL` differs on 1,184 records, all below 1e-10 (largest difference 4.8e-16).
- **Each chip reproduces itself:** the VCF body is byte-identical to the recipe's own earlier run on
  the same chip (Intel, AMD, and Graviton4 against the `c6g` rung). So the count is a fixed point
  across chips and the low-`QUAL` tail is an ARM-vs-x86 path.
- Spend for the two x86 legs: $0.053 billed. *Billed* is spawn's task window, wrapper start to
  stage-out done, in both tables ([`results/billed-windows.txt`](results/billed-windows.txt)).

## The even ladder is the finding

| code | Gv2→Gv5 | per-step | Gv3→Gv4 |
|---|---|---|---|
| **freebayes** (Bayesian caller) | **2.65×** | 1.59 / **1.32** / 1.27 | **1.32×** |
| [ambertools](../ambertools-real/README.md) (serial MD) | 2.50× | 1.52 / **1.16** / 1.42 | 1.16× |
| bedtools `genomecov` | 2.33× | 1.69 / 1.16 / 1.19 | 1.16× |
| HMMER | 1.90× | 1.43 / **1.06** / 1.26 | 1.06× |
| picard | 1.93× | 1.24 / 1.26 / 1.23 | 1.26× |
| SIESTA | 1.86× | 1.44 / **1.09** / 1.18 | 1.09× |

A pattern had been accumulating that Graviton3→4 is nearly free for likelihood and matrix codes —
HMMER at 1.06× is actually *dearer per result*, and ambertools and bedtools both sit at 1.16×.
**freebayes refutes the generalisation on its own terms:** it is a Bayesian haplotype caller, i.e.
squarely a likelihood code, and it gains **1.32×** at exactly that step. So the weak Gv3→Gv4 step is
a property of particular inner loops, not of likelihood methods as a class, and not of the step.

Together with ambertools this also moves the catalog's spread again. The gains now run **1.84×–2.65×**
([query 8](../../scripts/record-queries.sh), bwa to freebayes) — a 1.44× range — so quoting any single generational multiplier is wrong by up to 44% depending on
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

Raw smoke-check output per rung and the ladder as TSV are in [`results/`](results/). The x86
legs: `python3 scripts/spec_arch.py freebayes x86_64|amd <bucket> <nonce>
measurements/freebayes-real/chip.task.json`. On spawn 0.139.1 these exceed EC2's 16 KiB user data
(spawn#1063/#1246), so they ran with the inline command moved, byte for byte, into a staged
sha256-checked file.
