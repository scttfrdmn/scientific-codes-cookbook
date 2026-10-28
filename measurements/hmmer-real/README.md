# Graviton4 is the wrong buy for hmmsearch, on compute and on the bill

> **Graviton3 → Graviton4 costs 4% more per result on compute**, because it is only
> 1.06× faster for 10% more per hour. Four codes now show that rung failing; this is the first where
> the job is long enough that boot cannot be blamed.

HMMER 3.4, all 30,134 Pfam-A 38.2 families against 23,879 human proteins (one per gene) at `--cut_ga`,
8 vCPU on every rung, same image digest, same input objects.

| chip | instance | `hmmsearch` | billed | $/hr | **compute $** | billed $ |
|---|---|---|---|---|---|---|
| Graviton2 | `c6g.2xlarge` | 2514 s | 2609 s | 0.2720 | 0.1900 | 0.1971 |
| Graviton3 | `c7g.2xlarge` | 1763 s | 1836 s | 0.2900 | **0.1420** | **0.1479** |
| Graviton4 | `c8g.2xlarge` | 1669 s | 1845 s † | 0.3190 | 0.1479 | 0.1635 † |
| **Graviton5** | `c9g.2xlarge` | **1322 s** | 1375 s | 0.3478 | **0.1277** | 0.1328 |
| Intel Xeon 6975P-C (2 threads/core) | `c8i.2xlarge` | 1352 s | 1412 s | 0.3748 | 0.1408 | 0.1470 |
| **AMD EPYC 9R45** (1 thread/core) | `c8a.2xlarge` | **922 s** | 970 s | 0.4311 | **0.1104** | **0.1162** |

*Billed* is spawn's task window, wrapper start to stage-out done (completion records,
[`results/billed-windows.txt`](results/billed-windows.txt)). † Graviton4's window is unattributed: no
completion record of its 2026-10-01/02 run survives, and 1845 s matches no other source, so its billed
figures are not on the same basis as the other rows.

Per-step speedup: **1.43×, 1.06×, 1.26×.** Over the full ladder, 1.90×.

## Intel and AMD, same generation as Graviton4

Same script (`search.sh`, the recipe's own), same two input objects, bioconda's x86 build of HMMER 3.4
(`quay.io/biocontainers/hmmer@sha256:8ca0dc3b…`), 8 vCPU, `--cpu 8`.

- **Every assertion holds on both**: 30,134 models, 23,879 targets, `[ok]`, 65,607 hits, 9,473
  families, 22,740 proteins, exact.
- **The hit set is a fixed point; the scores are a path.** Both x86 chips return the same 65,607
  (protein, family) pairs as Graviton4, which is what the GA thresholds decide. Below that the
  tblout differs: 54 hits print a different E-value in the last digit, 16 a full-sequence score up
  to 0.2 bits apart, 2 a different domain-envelope count (a 45-domain Ig-like protein), and tied rows
  come out in a different order. The SIMD filters are NEON on one side and SSE/AVX on the other, so
  this is expected arithmetic, not a bug. **Intel and AMD agree byte for byte**
  (`results/cross-chip.txt`).
- **AMD is the fastest and cheapest box measured here**: 1.81× Graviton4 and 1.43× Graviton5, and
  $0.1104 per proteome on compute against Graviton5's $0.1277. Intel is 1.23× Graviton4 and costs
  5% less per result on compute. On `c8a` each of the 8 vCPUs is a full core; on `c8i` they are 4
  cores × 2 threads. That accounts for part of the AMD/Intel gap, not all of it. Nothing was
  measured that would split the rest.
- **The wall time repeats.** The recipe's own x86 verifying runs (same spec, earlier boxes) took
  1364 s (Intel) and 936 s (AMD), within 1.5% of these.

Spend for the two legs: $0.263. Raw: `results/smoke-check-{x86_64,amd}-2xl.txt`,
`results/host-{x86_64,amd}.txt`, `results/cross-chip.txt`.

## Why this reversal is more than n=1 noise

The catalog had three prior Graviton3→Graviton4 cost reversals — GPAW +1.4%, SIESTA +0.7%,
RAxML-NG +7.7% — and the first two are small enough to be a tie at n=1. hmmsearch is different in
three ways that matter:

- **Overhead is under 4% of the bill.** At 22–42 minutes of compute in a 23–43 minute window, the
  billed/compute ratio is ~1.04 on every attributed rung. Compute and billed therefore agree on the direction, which is not
  true for the short jobs in this catalog — [seqkit](../../recipes/seqkit/README.md) and
  [bedtools](../bedtools-real/README.md), where at 90–280 s of work the billed column carries a fixed
  startup cost that compute does not.
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

The Graviton4 rung is `gen-c8g.task.json` (the recipe's spec with its own output prefix); the Intel and AMD
legs are `python3 scripts/spec_arch.py hmmer {x86_64,amd} "$COOKBOOK_BUCKET" <nonce> measurements/hmmer-real/gen-c8g.task.json`.
Every `gen-*.task.json` stages its script as `search.sh` (sha256 in `pins.sha256`, with `host.sh`, a
diagnostic-only host record): spawn 0.139.1's own wrapper leaves under 1 KB of EC2's 16 KiB user data
(spawn#1246), so an inline script no longer fits. `search.sh` is the inline script byte for byte,
and the Graviton rungs ran it inline. Upload those three files to `inputs/hmmer-real/` first.

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
