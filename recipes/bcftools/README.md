# bcftools — pileup variant calling, cross-checked against freebayes

One task. bcftools (`mpileup` + `call`) calls variants on a 30× human region BAM, then the
smoke check confirms a plausible call set **and** a confident-SNV concordance against
`recipes/freebayes` — two independent caller models agreeing on the calls they're both sure of.

> **Why a 30× targeted fixture.** The first attempt reused bwa's ~0.3× chr20 subsample, where
> bcftools and freebayes concord at only ~0.34 Jaccard (measured) — not a bug in either tool
> but a depth too low to support confident calling, the like-with-like rule rejecting the
> comparison. A small **30× fixture** was staged (chr20:2,000,000–2,400,000, HG00096 1000G NYGC
> high-coverage, mean depth 35.2×, 19 zero-cov / 78 sub-10× bases of 400,001 — clean
> euchromatin), and the concordance becomes meaningful (**0.9103**). Not a benchmark.

## The cross-code metric, made like-with-like

bcftools uses a **pileup** model, freebayes a **haplotype** model — a raw VCF diff would fail
for a reason unrelated to correctness. So the check:

1. **normalises** both call sets (`bcftools norm -m-`: split multiallelics, left-align to the reference),
2. restricts to **confident SNVs** (`QUAL ≥ 20`) — SNVs because the two callers represent
   indels differently even after normalisation, so SNVs are the apples-to-apples set,
3. asserts **Jaccard(POS:REF:ALT) ≥ 0.85** (observed **0.9103**; intersection 609 / union 669).

The floor is what two correct germline callers should reach at 30× (literature 0.85–0.95 on
confident SNVs; the residual is complex / low-mappability loci) — **set by the shared problem,
not by shaving the observed value**. A broken caller decorrelates far below it.

## Pins

| | |
|---|---|
| image | `quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e` |
| | tag `1.24--hd65497c_0`, bcftools 1.24, cosign-verified (`publish.yml@refs/heads/main`), `linux/arm64` |
| BAM | `HG00096.chr20_2.0-2.4Mb.30x.bam`, `sha256:6949939b…21b04a` (+ `.bai`) |
| reference | `inputs/bwa-samtools/chr20.fa`, reused; `bcftools mpileup` auto-builds `.fai` (no samtools needed) |
| freebayes VCF | `runs/freebayes/r1/freebayes.vcf`, the cross-check counterpart |

**Data tier: stable public source with a durable id.** The 30× fixture's provenance is in
`stage-inputs.sh` in this directory (1000G NYGC high-coverage, region slice, pinned sha256s).

## Smoke check

Measured in the pinned image (`--user 1000:1000`, arm64).

| observable | assertion | observed |
|---|---|---|
| variants total | 650..950 | 806 |
| confident (QUAL ≥ 20) | 710..880 | 797 |
| **SNV concordance vs freebayes** | Jaccard ≥ 0.85 (confident SNVs) | **0.9103** |

## Resources, and what the timings mean

2 vCPU / 4 GiB, `c8g` (resolves to `c8g.large`), TTL 5m, cap $0.02. `mpileup`+`call` on the
30× region is **~1 s**; normalisation + concordance a few seconds more.

**These timings are not compute cost.** Boot, the Docker install, and pulling the bcftools
image are the whole task. The recorded run's window was **49s** (18:36:14 → 18:37:03 UTC),
confident-SNV Jaccard vs freebayes 0.9103. TTL/cap already minimal; no retighten needed. Disk is trivial.

## Running it

**Run `recipes/freebayes` first** — this task stages its VCF for the concordance step (the two
recipes are a resumable S3 chain; neither touches the other's local disk).

```sh
spawn task run --spec recipes/freebayes/01-call.task.json --wait
spawn task run --spec recipes/bcftools/01-call.task.json  --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/bcftools/r1/
```

The smoke check runs *inside* the task; the bucket listing is the second half of it. Expect
two objects (`bcftools.vcf.gz`, `smoke-check.txt`).

**Re-running.** `task_id` is fixed; bump the `-r1` suffix in both `task_id` and the output
prefix to keep both records. Fixture provenance / re-staging: `./stage-inputs.sh`.
