# freebayes — Bayesian haplotype variant calling, cross-checked against bcftools

One task. freebayes calls variants on a 30× human exon-region BAM; the smoke check confirms
a valid, plausibly-sized call set. Its VCF is then the cross-check counterpart for
`recipes/bcftools`, which computes a confident-SNV concordance between the two callers.

> **Why a 30× targeted fixture, not the bwa subsample.** The first attempt reused bwa's
> chr20 alignments — but that is a ~0.3× subsample built for *alignment* identities, and at
> that depth two correct callers concord at only ~0.34 Jaccard (measured), because confident
> calls are dominated by single-read events where their error models legitimately diverge.
> That is the like-with-like rule rejecting a comparison the data can't support. So a small
> **30× fixture** was staged (chr20:2,000,000–2,400,000, HG00096 1000G NYGC high-coverage,
> mean depth 35.2×, clean euchromatin) — and the concordance becomes meaningful (0.91). Not a
> benchmark; a proof that freebayes runs on Graviton4 and agrees with an unrelated caller.

## Two callers, one confident-SNV concordance

- **Per-tool (this recipe):** freebayes produces a valid VCF with a genotyped sample and a
  plausible confident-variant count (QUAL ≥ 20). Region-restricted to the fixture.
- **Cross-code (computed in `recipes/bcftools`, documented in both):** bcftools (pileup model)
  and freebayes (haplotype model) are *different algorithms* — a raw VCF diff fails by design.
  The honest metric normalises both (split multiallelics, left-align), restricts to
  **confident SNVs (QUAL ≥ 20)** — SNVs because the two represent indels differently even
  after normalisation, so SNVs are the apples-to-apples set — and asserts the **Jaccard of
  POS:REF:ALT ≥ 0.85** (observed **0.9103**). The floor is what two correct germline callers
  should reach at 30× (literature 0.85–0.95 on confident SNVs; the residual is complex /
  low-mappability loci), not the observed value shaved. A broken caller falls far below.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/freebayes@sha256:033f0f12b3a31db97ebceee72604c904d1436f877e288ff247f22a9eedfacdf9` |
| | tag `1.3.10--h1c6109c_0`, freebayes 1.3.10, cosign-verified (`sign-existing.yml@refs/heads/main`), `linux/arm64` |
| BAM | `HG00096.chr20_2.0-2.4Mb.30x.bam`, `sha256:6949939b…21b04a` (+ `.bai` `sha256:657150da…537e22`) |
| reference | `inputs/bwa-samtools/chr20.fa` (full chr20, matches the BAM header `LN:64444167`), reused |

**Data tier: stable public source with a durable id.** The 30× fixture's provenance (1000G
NYGC high-coverage, region slice, sha256s) is in `recipes/bcftools/stage-inputs.sh`.

## Smoke check

Measured in the pinned image (`--user 1000:1000`, arm64).

| observable | assertion | observed |
|---|---|---|
| VCF header | present (`##fileformat=VCF`) | yes |
| sample column | a genotyped sample present | yes |
| variants total | 1400..2200 (incl. freebayes's QUAL~0 tail) | 1797 |
| **confident (QUAL ≥ 20)** | 680..840 | 757 |

The cross-code SNV concordance (Jaccard 0.9103) is asserted in `recipes/bcftools`.

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. The call is **~5 s** on
the 30× 400 kb region.

**These timings are not compute cost.** Boot, the Docker install, and pulling the freebayes
image are the whole task. The recorded run's window was **55s** (18:33:39 → 18:34:34 UTC),
1797 variants / 757 confident. TTL/cap already minimal; no retighten needed. Disk is trivial.

## Running it

**Run this recipe first** — `recipes/bcftools` stages this VCF for the concordance step.

```sh
spawn task run --spec recipes/freebayes/01-call.task.json --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/freebayes/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
two objects (`freebayes.vcf`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records.
