# QUAST — the independent cross-validator for SPAdes and MEGAHIT

The join of a three-recipe assembly chain. QUAST reads **both** assemblies — SPAdes' and
MEGAHIT's, of the same fixed reads — and the smoke check asserts each assembler's exact
metrics *as QUAST reports them*. One unrelated tool measuring two, which is a stronger
construction than comparing the assemblers to each other.

> **What this recipe does and does not cover.** It runs QUAST on two staged assemblies and
> asserts the exact contig counts (raw and ≥500 bp) and N50 per assembler — enough to prove
> QUAST evaluates assemblies correctly on Graviton4 and to cross-validate the two assembler
> recipes. Not a benchmark; no reference-based misassembly analysis.

## Why QUAST rather than SPAdes-vs-MEGAHIT directly

Two assemblers on the same reads produce **different** contig sets by design (different
algorithms) — comparing them to each other would measure the algorithm difference, not
correctness (the CLAUDE.md "compare like with like" rule). Instead one independent tool
measures each, and the recipe asserts each assembler's own deterministic numbers. QUAST's
default `# contigs` applies a ≥500 bp filter, so it reports fewer than the raw FASTA
(SPAdes 237 → 78; MEGAHIT 2 → 1); the recipe asserts both the raw (`# contigs (>= 0 bp)`)
and the filtered counts, plus N50 and total length.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/quast@sha256:54122e645394aa741656c54ecdde8737b2ad8cc0ef6ead72392c1be8248ae692` |
| | tag `5.3.0`, QUAST 5.3.0, cosign-verified (`sign-existing.yml`), `linux/arm64` |
| inputs | `runs/spades/r1/contigs.fasta` + `runs/megahit/r1/contigs.fa` (the two assemblers' outputs) |

**Data tier: derived — the two assembler outputs.** This task runs *after* SPAdes and
MEGAHIT (a resumable S3 chain; each reads its inputs from S3 and writes to S3).

## Smoke check (per assembler, from QUAST's `report.tsv`)

| metric | SPAdes | MEGAHIT |
|---|---|---|
| `# contigs (>= 0 bp)` (raw) | 237 | 2 |
| `# contigs` (≥500 bp) | 78 | 1 |
| N50 | 33380 | 400429 |
| Total length | 399846 | 400429 |

All exact-or-wrong: each is a deterministic function of a deterministic assembly, measured
by an independent tool.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. QUAST was **~a few
seconds** of compute locally.

**These timings are not compute cost.** Boot + Docker install + image pull dominate. Recorded
window **73s** (21:48:19 → 21:49:32 UTC), spades 237/N50 33,380 and megahit 2/N50 400,429 exact.
TTL/cap already minimal. Disk is trivial.

## Running it

Run `recipes/spades` and `recipes/megahit` first (QUAST reads their staged contigs), then:

```sh
spawn task run --spec recipes/quast/01-evaluate.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/quast/r1/
```

Expect `report.tsv` + `smoke-check.txt`.

**Re-running.** Bump the `-r1` suffix in `task_id` and the output prefix.
