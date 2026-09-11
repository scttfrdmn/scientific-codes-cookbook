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

The recipe calls a 30× human region and cross-checks it against [freebayes](../freebayes/README.md) where both callers are confident.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× region BAM (chr20:2.0–2.4 Mb, HG00096) | your own aligned BAM | `mpileup` auto-builds the `.fai`; no separate samtools step. |
| chr20 reference slice | your reference | one `-f` fasta; bcftools indexes it for you. |
| **30× coverage** | keep real coverage | **load-bearing** — a first ~0.3× subsample collapsed confident calling (concordance 0.34); germline calling needs real depth. |

`bcftools call` is deterministic — **nothing here is determinism scaffolding**. The one scale-it that earned it is the depth above: a ~0.3× fixture misrepresented the tool, settled at 30×.

## Shape, size, cost

One task, ~1 s of calling. `c8g.large`, ~$0.02, **~49s** wall — boot and image pull, not bcftools ([why](../../practices/what-this-does-not-cover.md)). Depends on [freebayes](../freebayes/README.md) for the cross-check (an S3 chain — run it first).

<details>
<summary>As shipped: the like-with-like cross-code check, pins, smoke check</summary>

bcftools uses a **pileup** model, freebayes a **haplotype** model, so a raw VCF diff would compare methods, not correctness ([compare like with like](../../practices/cross-checks.md)). Made apples-to-apples: **normalise** both (`bcftools norm -m-`), restrict to **confident SNVs** (`QUAL ≥ 20`; the models represent indels differently even after norm), assert **Jaccard(POS:REF:ALT) ≥ 0.85** — observed **0.9103** (609/669). The 0.85 floor is what two correct germline callers reach at 30× (literature 0.85–0.95), not a shaved value.

| observable | assertion | observed |
|---|---|---|
| variants total | 650–950 | 806 |
| confident (QUAL ≥ 20) | 710–880 | 797 |
| SNV concordance vs freebayes | Jaccard ≥ 0.85 | 0.9103 |

**Pins.** Image `quay.io/aarchbio/bcftools@sha256:8171fe744646…` (1.24, cosign-verified, `linux/arm64`). BAM `HG00096.chr20_2.0-2.4Mb.30x.bam` (`sha256:6949939b…`, 1000G NYGC high-coverage slice — provenance + re-stage via `make stage RECIPE=bcftools`; reference `inputs/bwa-samtools/chr20.fa` (reused); freebayes VCF from `runs/freebayes/r1/`.

**Run + verify.**
```sh
make run RECIPE=freebayes   # cross-check counterpart, first
make run RECIPE=bcftools
make ls RECIPE=bcftools   # expect bcftools.vcf.gz, smoke-check.txt
```
Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

</details>
