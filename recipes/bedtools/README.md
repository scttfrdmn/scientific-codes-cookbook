# bedtools — genome-interval set algebra, exact by construction

One task. bedtools runs four interval operations on two small constructed BED files,
and the smoke check asserts every output integer against a value **derived by hand** —
so this is exact-or-wrong (algorithmic, no band), the same class of check as BLAST's
self-hit or salmon's TPM sum.

> **What this recipe does and does not cover.** It exercises `merge`, `intersect`,
> `subtract` and `genomecov` on fixed intervals with hand-computed answers — enough to
> prove bedtools' core set algebra is correct on Graviton4. Not a benchmark; no large
> annotation set or real-genome interval workload.

## The intervals, and why the answers are exact

Two BED files on a single chromosome `chr1` (half-open coordinates, as BED uses):

```
a.bed            b.bed
chr1  0   100    chr1  75   125
chr1  50  150    chr1  250  350
chr1  200 300
```

Because the intervals are fixed and small, each operation's result is computable by
hand — the recipe asserts those integers exactly, and a reader can check the
derivation:

- **`merge a.bed`** → `[0,100]` and `[50,150]` overlap (50 < 100) so they collapse to
  `[0,150]`; `[200,300]` stands alone. **2 intervals, 150 + 100 = 250 bp.**
- **`intersect -a a -b b`** → the clipped overlaps are `[75,100]` (25), `[75,125]` (50)
  and `[250,300]` (50). **3 intervals, 25 + 50 + 50 = 125 bp.**
- **`subtract -a a -b b`** → removing B from A gives `[0,75]` (75), `[50,75]` + `[125,150]`
  (25 + 25), and `[200,250]` (50). **4 intervals, 175 bp.**
- **`genomecov -i a -g genome`** (genome length 400) → depth histogram: depth 0 over
  `[150,200)+[300,400)` = **150**, depth 1 over `[0,50)+[100,150)+[200,300)` = **200**,
  depth 2 over `[50,100)` = **50**. The three bin sizes **sum to 400 = the genome
  length** — a conservation identity: every base is counted at exactly one depth.

The `genomecov` sum is the standout — a conservation check that falls out of the
operation being correct, not a threshold on an observed value.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/bedtools@sha256:cd1e72a29500369c5576c10e98b2c1723a09a73bde9fb50d80bf4022096b449b` |
| | tag `2.31.1--h63bafd0_3`, `linux/arm64`, cosign-verified (aarchbio `sign-existing.yml`) |
| input | two BED files + a genome file, **inline in the task** — nothing staged |

**Data tier: none / in-task.** The intervals are written in the command; there is no
external input to pin.

## Smoke check

Measured in this image, before any launch — every value matches its hand-derivation.

| observable | assertion | observed |
|---|---|---|
| merge intervals | exactly 2 | 2 |
| merge bp | 250 (150 + 100) | 250 |
| intersect intervals | exactly 3 | 3 |
| intersect bp | 125 (25 + 50 + 50) | 125 |
| subtract intervals | exactly 4 | 4 |
| subtract bp | 175 (75 + 25 + 25 + 50) | 175 |
| genomecov depth-0 bases | 150 | 150 |
| genomecov depth-1 bases | 200 | 200 |
| genomecov depth-2 bases | 50 | 50 |
| **genomecov total** | 400 = genome length (conservation) | 400 |

No bands anywhere — bedtools' interval algebra is deterministic and the answers are
defined by the inputs.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The recorded run's window
was **47s** (05:28:37 → 05:29:24 UTC), every exact integer matched. TTL/cap already minimal;
no retighten needed. The four operations
are **sub-second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the
bedtools image are the whole task. The first-run TTL is 5m and will be **retightened
from the first real run** — a loose TTL is a larger blast radius, not caution. Disk is
trivial.

## Running it

No `stage-inputs.sh` — the intervals are inline.

```sh
spawn task run --spec recipes/bedtools/01-setops.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/bedtools/r1/
```

The smoke check runs *inside* the task, and the bucket listing is the second half of
it. Expect five objects (`merge.bed`, `intersect.bed`, `subtract.bed`, `genomecov.txt`,
`smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the
output prefix to keep both records.
