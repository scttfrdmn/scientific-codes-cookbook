# Graviton4 is the wrong buy for hmmsearch, on compute and on the bill

> **Graviton3 → Graviton4 costs 4% more per result on compute and 6% more billed**, because it is only
> 1.06× faster for 10% more per hour. Four codes now show that rung failing; this is the first where
> the job is long enough that boot cannot be blamed.

HMMER 3.4, all 30,134 Pfam-A 38.2 families against 23,879 human proteins (one per gene) at `--cut_ga`,
8 vCPU on every rung, same image digest, same input objects.

| generation | instance | `hmmsearch` | billed | $/hr | **compute $** | billed $ |
|---|---|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 2514 s | 2760 s | 0.2720 | 0.1900 | 0.2085 |
| Graviton3 | `c7g.2xlarge` | 1763 s | 1911 s | 0.2900 | **0.1420** | **0.1539** |
| Graviton4 | `c8g.2xlarge` | 1669 s | 1845 s | 0.3190 | 0.1479 | 0.1635 |
| **Graviton5** | `c9g.2xlarge` | **1322 s** | 1421 s | 0.3478 | **0.1277** | 0.1373 |

Per-step speedup: **1.43×, 1.06×, 1.26×.** Over the full ladder, 1.90×.

## Why this reversal is more than n=1 noise

The catalog had three prior Graviton3→Graviton4 cost reversals — GPAW +1.4%, SIESTA +0.7%,
RAxML-NG +7.3% — and the first two are small enough to be a tie at n=1. hmmsearch is different in
three ways that matter:

- **Boot is a tenth of the bill.** At 28–46 minutes of compute in a 31–46 minute window, the
  billed/compute ratio is ~1.10. Compute and billed therefore agree on the direction, which is not
  true for the short jobs in this catalog — [seqkit](../../recipes/seqkit/README.md) and
  [bedtools](../bedtools-real/README.md) both show an apparent Gv3-beats-Gv4 on *billed* that
  disappears on compute, because at 90–280 s the billed column is mostly boot.
- **The time step is the anomaly, not the price.** The rate card rises a steady ~9–10% per
  generation here; what is unusual is 1.06× of speed for it, against 1.43× and 1.26× either side.
- **The result is identical across all four rungs** (65,607 hits / 9,473 families / 22,740 proteins,
  to the digit), so the comparison is not confounded by the runs doing different amounts of work.

Why the step is weak is **not established**. `hmmsearch`'s inner loop is a vectorised Viterbi filter,
so a SIMD-width or memory-bandwidth measurement would be needed to attribute it; a wall clock cannot.
Noting the shape and refusing the explanation is the honest stopping point.

## The sweep as confirmation, not just a price list

The three hit counts went into the spec as **exact assertions before the sweep ran**, not after. That
is the whole reason the sweep is worth its cost twice: `hmmsearch` is deterministic on fixed inputs,
so three further generations reproducing 65,607 / 9,473 / 22,740 is what turns a recorded observation
into a verified number. Had they been added afterwards, they would have been confirmed by the single
run that produced them — which is no confirmation at all.

`[ok]` is the assertion that would actually catch a bad run here. `hmmsearch` writes that literal as
its last line only on a clean exit, so a search killed by a TTL or an OOM fails even though its
partial `tblout` would sit inside any plausible hit-count band.

## Sizing: the extrapolation was close, and that was partly luck

TTL was sized by scaling the old recipe's measured 192 s (200 models against all 382,428 isoforms) by
the ratio of work: `(30134/200) × (23879/382428) = 9.41×`, predicting ~30 minutes against the measured
**27.8**. Close — but the 200 models were the first 200 of the release, not a random sample, and model
length varies, so the agreement is partly luck. The sweep's TTLs were then sized from the *measured*
Graviton4 number plus the 1.84–2.43× span every other ladder here spans, which is the better basis.

Retightened after the fact: the shipped TTL came down 60m → 55m.

## Run it

```sh
export AWS_PROFILE=aws COOKBOOK_BUCKET=<your bucket>
make stage RECIPE=hmmer
make run   RECIPE=hmmer                    # the Graviton4 rung, as shipped
spawn task run --spec measurements/hmmer-real/gen-c6g.task.json --wait
spawn task run --spec measurements/hmmer-real/gen-c7g.task.json --wait
spawn task run --spec measurements/hmmer-real/gen-c9g.task.json --wait
```

Launch these **serially**. Eight concurrent `spawn task run` calls throttled the AWS Price List API
earlier in this batch, and spawn correctly refuses to launch rather than drop an unenforceable cost
cap — filed as [truffle#175](https://github.com/spore-host/truffle/issues/175), since the static
fallback table has no Graviton coverage at all. `make run` substitutes `${COOKBOOK_BUCKET}`;
`spawn task run` does not.

## Caveats

n = 1 per generation, defensible because the *result* is identical on all four and the per-step
speedups bracket the anomaly on both sides. The 4% cost reversal is a single pair of observations and
should be repeated before anyone builds a purchasing policy on the exact figure; the *direction* is
corroborated by three other codes.

One instance size, one proteome, one HMM library. A larger proteome would scale runtime linearly and
should not move the ratios; a library of much shorter models might.
