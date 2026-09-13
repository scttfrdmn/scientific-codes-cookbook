---
tool: freebayes
tool_version: 1.3.10
image: quay.io/aarchbio/freebayes@sha256:033f0f12b3a31db97ebceee72604c904d1436f877e288ff247f22a9eedfacdf9
spawn_version: 0.104.0
---
# freebayes — Bayesian haplotype variant calling

A different model from the pileup callers: freebayes assembles haplotypes and calls variants from them.

## Run it

```bash
freebayes -f ref.fa aln.bam > calls.vcf
```

The recipe calls the same 30× human region as [bcftools](../bcftools/README.md), and its VCF is the counterpart for the confident-SNV concordance the two compute between them.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× region BAM (chr20:2.0–2.4 Mb, HG00096) | your own aligned BAM | one `-f` reference, one BAM; region-restricted here to the fixture. |
| **30× coverage** | keep real coverage | **load-bearing** — freebayes's confident calls are dominated by multi-read haplotype support; at the ~0.3× subsample the first attempt used, two correct callers concord at only 0.34. Depth is the point. |

`freebayes` is deterministic on a fixed BAM — **nothing here is determinism scaffolding**. Like bcftools, the depth is a scale-it that earned it (a shallow fixture misrepresented the caller), already settled at 30×.

## Shape, size, cost

One task, ~5 s of calling. `c8g.large`, ~$0.02, **~55s** wall — boot and image pull, not freebayes ([why](../../practices/what-this-does-not-cover.md)). **Run this before [bcftools](../bcftools/README.md)** — bcftools reads this VCF for the cross-check (S3 chain).

**Sizing:** ~5 s and memory-modest on this single-sample region; freebayes grows memory with **depth × sample count** — deep WGS or joint multi-sample calling wants an `r` box, so size to those, not this fixture.

<details>
<summary>As shipped: the cross-code concordance, pins, smoke check</summary>

Per-tool, this recipe just confirms a valid, genotyped, plausibly-sized VCF. The **cross-code** check — pileup vs haplotype, [compared like with like](../../practices/cross-checks.md) — lives in [bcftools](../bcftools/README.md): normalised, confident SNVs (QUAL ≥ 20), Jaccard ≥ 0.85 (observed **0.9103**).

| observable | assertion | observed |
|---|---|---|
| VCF header + genotyped sample | present | yes |
| variants total (incl. QUAL~0 tail) | 1400–2200 | 1797 |
| confident (QUAL ≥ 20) | 680–840 | 757 |

freebayes emits a large QUAL~0 tail by design, hence the wide total band; the confident count is the meaningful one.

**Pins.** Image `quay.io/aarchbio/freebayes@sha256:033f0f12b3a3…` (1.3.10, cosign-verified, `linux/arm64`). BAM `HG00096.chr20_2.0-2.4Mb.30x.bam` (`sha256:6949939b…`); reference `inputs/bwa-samtools/chr20.fa` (full chr20, matches the BAM header). Provenance/re-stage: `make stage RECIPE=bcftools`.

**Run + verify.**
```sh
make run RECIPE=freebayes
make ls RECIPE=freebayes   # expect freebayes.vcf, smoke-check.txt
```
Re-run: `make run` launches a fresh task each time and overwrites this prefix — no spec edit needed.

**Fan out across samples.** One variant call is one task; a cohort is the same task as a [job array](../../patterns/job-arrays.md) — validate on one sample with `make run` above, *then* fan out one instance per sample, each keyed by `$JOB_ARRAY_INDEX`. `spawn array status` / `collect` / `retry --failed` manage the set; add `--max-concurrent-auto` when a shared reference or spot capacity pushes back.

</details>
