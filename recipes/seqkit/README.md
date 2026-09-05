# seqkit — exact FASTQ statistics, a concatenation conservation identity

One task. seqkit computes summary statistics over the two mates of a fixed read set and the
smoke check asserts them exactly — including that the concatenation of the two files sums to
exactly their two totals (a conservation identity, no band).

> **What this recipe does and does not cover.** It runs `seqkit stats` over a fixed 51,933-pair
> read set and asserts exact counts/lengths — enough to prove seqkit works on Graviton4. Not a
> benchmark; no large FASTX manipulation pipeline.

## Exact identities (seqkit is deterministic; reads are fixed)

- **Per-mate counts.** Each mate: `num_seqs` = 51933, `sum_len` = 7,789,950 bp, read length a
  uniform 150 (min == max == 150). Exact-or-wrong.
- **Concatenation conservation.** `cat` of the two mates has `num_seqs` = 103,866 and `sum_len`
  = 15,579,900 — and the smoke check asserts these equal `r1 + r2` (`103866 == 51933+51933`,
  `15579900 == 7789950+7789950`), so a reader sees the identity is a conservation law, not two
  coincidental numbers.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/seqkit@sha256:5478aaad4dd7bf7d7f02eee168ee3ad90d17b6729ab5e889a9385b4458cde7c5` |
| | tag `2.13.0--h8865c2f_0`, seqkit 2.13.0, cosign-verified (`publish.yml@refs/heads/main`), `linux/arm64` |
| input | the 30× fixture's reads, `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` (sha256 `4f43bf36…` / `93f4a677…`) |

**Data tier: reused.** The reads are `samtools fastq` of the shared 30× BAM fixture
(`recipes/bcftools/stage-inputs.sh`) — no new staging; nothing re-derived under a second prefix.

## Smoke check

Measured in the pinned image (validated verbatim, `--user 1000:1000`).

| observable | assertion | observed |
|---|---|---|
| r1 num_seqs / sum_len | 51933 / 7789950 | 51933 / 7789950 |
| r2 num_seqs / sum_len | 51933 / 7789950 | 51933 / 7789950 |
| **combined num_seqs** | 103866 (== r1+r2) | 103866 |
| **combined sum_len** | 15579900 (== r1+r2) | 15579900 |
| read length | uniform 150 (min == max) | 150 / 150 |

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. `seqkit stats` is
**sub-second**.

**These timings are not compute cost.** Boot, the Docker install, and pulling the small seqkit
image are the whole task. Recorded window **47s** (21:44:31 → 21:45:18 UTC), stats exact. TTL/cap
already minimal. Disk
is trivial.

## Running it

No `stage-inputs.sh` — the reads are already staged (shared 30× fixture).

```sh
spawn task run --spec recipes/seqkit/01-stats.task.json --wait
```

Then **check the bucket**:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/seqkit/r1/
```

The smoke check runs inside the task; the bucket listing is the second half. Expect two objects
(`stats.tsv`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in `task_id` and the output prefix.
