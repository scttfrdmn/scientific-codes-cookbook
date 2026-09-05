# fastp — read QC and trimming, checked by a conservation identity

One task. fastp quality-filters and trims the same 400,000 paired reads bwa aligned, and
the smoke check confirms an exact **conservation identity**: every input read is accounted
for as either passed or filtered, the paired mates stay paired, and the written output count
matches the passed count — all exact integers, no bands.

> **What this recipe does and does not cover.** It runs fastp's default QC + adapter/quality
> trimming on one paired-end sample and checks the read bookkeeping — enough to prove fastp
> works on Graviton4 and its filtering is internally consistent. Not a benchmark; no
> UMI/dedup/overrepresentation analysis.

## Why a conservation identity, not a cross-code check

fastp is a single-tool QC step with no natural sibling to cross-validate against on the same
bytes, so the honest strongest claim is a **conservation identity** — the same class as
salmon's TPM sum and bedtools' genomecov: fastp's own report must balance its books.
`reads_before = passed_filter + low_quality + too_many_N + too_short + too_long`, exactly,
and the two output FASTQs must carry equal read counts (paired-end integrity) summing to the
passed count. fastp is deterministic on fixed input, so every number is asserted exactly —
a truncated or mis-split output fails the arithmetic even though the run exits 0.

## Reusing bwa's staged reads

The reads are **bwa's already-staged bytes, verified against the same sha256** the
`recipes/bwa-samtools` align task pins (`4bd24cd…` / `ebd1ad5…`) — nothing is re-staged.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/fastp@sha256:061ee7c6b8e5af265dfed6f25c51e482e3bb403c51f167561405010e5c5f632a` |
| | tag `1.3.6--h5eda1b2_0`, fastp 1.3.6, cosign-verified (`publish.yml@refs/heads/main`), `linux/arm64` |
| reads | ENA `SRR062634` (HG00096, 1000 Genomes), first 400,000 pairs — reused from `recipes/bwa-samtools` |
| | `sha256:4bd24cd…3536a00` / `sha256:ebd1ad5…0d82a04` |

**Data tier: stable public source with a durable id — reused from `recipes/bwa-samtools`.**
Nothing is staged by this recipe; run `recipes/bwa-samtools/stage-inputs.sh` first if needed.

## Smoke check

Measured in the pinned image (`--user 1000:1000`).

| observable | assertion | observed |
|---|---|---|
| reads_before | exactly 800000 (400k pairs × 2) | 800000 |
| **accounted_for** | passed + low_quality + too_many_N + too_short + too_long **== reads_before** | 800000 |
| out1 == out2 | paired mates stay paired | 374058 = 374058 |
| out1 + out2 | == passed_filter | 748116 |

Component counts (reported, folded into the conservation sum): passed 748116, low_quality
51656, too_many_N 228, too_short 0, too_long 0.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. fastp on 400k pairs is
**~2 s** locally.

**These timings are not compute cost.** Boot, the Docker install, and pulling the small fastp
image are the whole task. The recorded run's window was **47s** (18:33:29 → 18:34:16 UTC),
the read counts balancing exactly. TTL/cap already minimal; no retighten needed. Disk is trivial.

## Running it

Reads come from `recipes/bwa-samtools` — stage those first if needed, then:

```sh
spawn task run --spec recipes/fastp/01-qc.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/fastp/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
four objects (`out_1.fq.gz`, `out_2.fq.gz`, `fastp.json`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
