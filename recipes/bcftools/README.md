---
tool: bcftools
tool_version: "1.24"
image: quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
spawn_version: 0.104.0
---
# bcftools — call variants from a pileup

The workhorse germline caller: pile up the reads, call the variants, get a VCF.

## Run it

```bash
bcftools mpileup -f ref.fa aln.bam | bcftools call -mv -Oz -o calls.vcf.gz
```

The recipe calls a 30× human region and then cross-checks the result against [freebayes](../freebayes/README.md) — two independent caller models agreeing on the variants they're both sure of.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× region BAM (chr20:2.0–2.4 Mb, HG00096) | your own aligned BAM | `mpileup` auto-builds the `.fai`; no separate samtools step. |
| chr20 reference slice | your reference | one `-f` fasta; bcftools indexes it for you. |
| **30× coverage** | keep real coverage | **load-bearing, not incidental** — the first attempt reused a ~0.3× subsample and confident calling fell apart (concordance 0.34). Depth is what makes germline calling meaningful; don't hand it a shallow fixture. |

`bcftools call` is deterministic — **nothing here is determinism scaffolding**, no seed to pin. The one thing that *had* to change was the input depth, and it already has: this is a scale-it that earned it (a ~0.3× fixture actively misrepresented the tool), settled at 30×.

## Shape, size, cost

One task, ~1 s of calling. `c8g.large`, ~$0.02, **~49s** wall — boot and image pull, not bcftools ([why](../../practices/container-path.md)). Depends on [freebayes](../freebayes/README.md) for the cross-check (an S3 chain — run it first).

<details>
<summary>As shipped: the like-with-like cross-code check, pins, smoke check</summary>

bcftools uses a **pileup** model, freebayes a **haplotype** model — a raw VCF diff would fail for a reason unrelated to correctness, so the check is made apples-to-apples: **normalise** both (`bcftools norm -m-`: split multiallelics, left-align), restrict to **confident SNVs** (`QUAL ≥ 20`; SNVs because the two represent indels differently even after norm), and assert **Jaccard(POS:REF:ALT) ≥ 0.85** (observed **0.9103**, intersection 609 / union 669). The floor is what two correct germline callers reach at 30× (literature 0.85–0.95; the residual is complex/low-mappability loci) — set by the shared problem, not shaved to the observed value; a broken caller decorrelates far below it.

| observable | assertion | observed |
|---|---|---|
| variants total | 650–950 | 806 |
| confident (QUAL ≥ 20) | 710–880 | 797 |
| SNV concordance vs freebayes | Jaccard ≥ 0.85 | 0.9103 |

**Pins.** Image `quay.io/aarchbio/bcftools@sha256:8171fe744646…` (1.24, cosign-verified, `linux/arm64`). BAM `HG00096.chr20_2.0-2.4Mb.30x.bam` (`sha256:6949939b…`, 1000G NYGC high-coverage slice — provenance + re-stage in `./stage-inputs.sh`); reference `inputs/bwa-samtools/chr20.fa` (reused); freebayes VCF from `runs/freebayes/r1/`.

**Run + verify.**
```sh
spawn task run --spec recipes/freebayes/01-call.task.json --wait   # cross-check counterpart, first
spawn task run --spec recipes/bcftools/01-call.task.json  --wait
aws s3 ls s3://scicookbook-942542972736-us-east-1/runs/bcftools/r1/   # expect bcftools.vcf.gz, smoke-check.txt
```
Re-running: bump the `-r1` suffix in `task_id` and the output prefix.

</details>
