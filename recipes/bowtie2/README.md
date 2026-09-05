# Bowtie 2 ← reuses bwa's reads, cross-checked against bwa mem

One task. Bowtie 2 aligns the **same 400,000 read pairs bwa already aligned** to the
same GRCh38 chr20, and the smoke check confirms a conservation identity plus a
cross-code agreement with bwa on which reads map — the two aligners run on identical
bytes, so the agreement means something.

> **What this recipe does and does not cover.** It builds a chr20 index and aligns a
> 400k-pair HG00096 slice, then cross-checks the mapped set against `recipes/bwa-samtools`
> — enough to prove Bowtie 2 works on Graviton4 and agrees with an independent aligner on
> identical input. Not a benchmark; no variant calling or full-genome alignment. Mapping
> rate is low (~26%) **by design** — the reference is chr20 only, so most reads have no
> home in it.

## The cross-check, and why `--local` (a finding worth keeping)

bwa mem does **local** alignment (it soft-clips the part of a read that doesn't match and
keeps the rest); Bowtie 2's **default is end-to-end** (global) alignment, which requires
the whole read to align or calls it unmapped. On this slice that difference is large and
real: Bowtie 2 default maps **11.25%** and agrees with bwa on only **82%** of reads,
because it rejects exactly the reads bwa soft-clips. Running Bowtie 2 in **`--local`**
mode — the apples-to-apples match to bwa mem — lifts the mapped rate to **25.77%** (vs
bwa's 29.13%) and the mapped-set agreement to **94.6%**. So the recipe uses `--local`,
and the residual ~5% disagreement is reads sitting at the local-alignment score
threshold, where two different scoring schemes legitimately differ — a **method-limited**
tolerance, not one picked to pass.

Position agreement is deliberately **not** asserted: with a chr20-only, repeat-heavy
slice, both aligners often find a different equally-valid placement for the same read, so
leftmost-position concordance is low (~32%) and says nothing about correctness. The
mapped-**set** concordance is the honest cross-code identity here.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/bowtie2@sha256:a6807f0611a1c276235f47d175471ebbaec863aa751a0c3771c324be57d8fc59` |
| | tag `2.5.5--hf3d0eb7_0`, Bowtie 2 2.5.5, cosign-signed (`sign-existing.yml`), `linux/arm64` |
| input (reused) | `inputs/bwa-samtools/chr20.fa` — GRCh38 chr20, sha256 `61eba5b0…` |
| input (reused) | `inputs/bwa-samtools/HG00096_chr20smoke_1.fq.gz` — sha256 `4bd24cdf…` |
| input (reused) | `inputs/bwa-samtools/HG00096_chr20smoke_2.fq.gz` — sha256 `ebd1ad56…` |
| cross-check ref | `runs/bwa-samtools/r1/aln.sam` — bwa's alignment of the same reads |

**Data tier: reused, not re-staged.** These are the exact bytes `recipes/bwa-samtools`
staged (a chr20 slice of the GRCh38 reference + a 400k-pair HG00096 slice from the 1000
Genomes RODA mirror). Pointing Bowtie 2 at the same objects is what makes the cross-check
valid — no second copy to keep true. **No `stage-inputs.sh`** — see `recipes/bwa-samtools`
for how they were built.

## Smoke check

Measured in the pinned image on arm64, before any launch.

| observable | assertion | observed |
|---|---|---|
| primary records | exactly **800000** (400k pairs × 2) — conservation | 800000 |
| primary mapped | 150000–260000 | 206155 |
| overall alignment rate | 22–29 % (`--local`) | 25.77 % |
| shared read-mates with bwa | exactly 800000 (identical read set) | 800000 |
| **concordance vs bwa** | **≥ 0.90** (mapped-set agreement) | **0.9462** |

The `800000` is a conservation check (reads in = primary records out), not a threshold.
Bowtie 2 is deterministic, so the counts reproduce exactly; the bands exist only to
survive an aligner-version change, and the concordance floor is method-justified, so none
of these can go flaky.

## Resources, and what the timings mean

4 vCPU / 8 GiB, `c8g` (resolves to `c8g.xlarge`), TTL 5m, cap $0.02.
`bowtie2-build` on chr20 was **35s**, the `--local` alignment **35s**, the concordance
check a few seconds — all compute-bound and well within an 8 GiB box (no memory pressure,
unlike `recipes/kallisto`).

**These timings are not compute cost.** Boot, the Docker install, and pulling the small
`bowtie2` image are most of the task. The recorded run's window was **114s** (05:28:10 →
05:30:04 UTC), concordance vs bwa 0.9462. TTL was **retightened from that first real run**:
10m → **5m**, `cost_limit` $0.03 → $0.02; the recorded run used the original 10m. A loose TTL
is a larger blast radius, not caution. Disk is modest (chr20 + index + reads + bwa's SAM ≈ 0.7 GiB).

## Running it

No `stage-inputs.sh` — the inputs are already in the bucket from `recipes/bwa-samtools`.

```sh
spawn task run --spec recipes/bowtie2/01-align.task.json --wait
```

Then **check the bucket**, every time:

```sh
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/bowtie2/r1/
```

The smoke check runs *inside* the task, and the bucket listing is the second half of it.
Expect three objects (`smoke-check.txt`, `align.log`, `build.log`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
