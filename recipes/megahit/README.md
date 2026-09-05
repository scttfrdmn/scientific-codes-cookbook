# MEGAHIT — de novo assembly, cross-validated by QUAST

One task in a three-recipe chain. MEGAHIT assembles the same staged reads SPAdes uses; the
smoke check asserts its exact, deterministic assembly, and `recipes/quast` measures it
alongside SPAdes' as the independent cross-validator.

> **What this recipe does and does not cover.** It assembles a ~400 kb chr20 region from
> ~35× reads and asserts the exact contig count and total length — enough to prove MEGAHIT
> runs deterministically on Graviton4. Not a benchmark; not a whole-genome assembly.

## Deterministic assembly is the identity

MEGAHIT (`-t 4 --min-count 2`) is **deterministic** — verified by assembling the same reads
twice for an identical result (2 contigs, 400,811 bp both times). The recipe asserts those
exact integers. MEGAHIT recovers this ~400 kb region as essentially two long contigs; SPAdes
(`recipes/spades`) fragments it into 237 — two correct assemblers, legitimately different
outputs, which is exactly why they are measured *independently* by QUAST rather than compared
to each other (the like-with-like rule).

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/megahit@sha256:d82953bf0096098b0b892edf7180f99b599e8ad17be14c47b1e8c2e1b6a8bdfd` |
| | MEGAHIT, cosign-verified (`sign-existing.yml`), `linux/arm64` |
| reads | `inputs/highcov/HG00096.chr20_2.0-2.4Mb.30x_reads_{1,2}.fq.gz` (51,933 pairs) — same bytes SPAdes assembles |

**Data tier: reused shared fixture** (the 30× fixture's reads; sha256-pinned, re-verified on the box).

## Smoke check

| observable | assertion | observed |
|---|---|---|
| contigs | exactly 2 (deterministic, `-t 4`) | 2 |
| total length | exactly 400811 bp | 400811 |

## Resources, and what the timings mean

4 vCPU / 8 GiB, `c8g` (resolves to `c8g.xlarge`), TTL 5m, cap $0.02. Assembly was **~10 s**
locally, no memory pressure.

**These timings are not compute cost.** Boot + Docker install + image pull dominate. Recorded
window **58s** (21:44:27 → 21:45:25 UTC), 2 contigs / 400,811 bp exact. TTL/cap already minimal.
Disk is trivial.

## Running it

```sh
spawn task run --spec recipes/megahit/01-assemble.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/megahit/r1/
```

Expect `contigs.fa` + `smoke-check.txt`; `recipes/quast` consumes `contigs.fa` from here.

**Re-running.** Bump the `-r1` suffix in `task_id` and the output prefix.
