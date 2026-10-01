---
tool: minimap2
tool_version: 2.31-r1302
image: quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a
images:
  - quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
spawn_version: 0.111.4
last_verified: 2026-10-01
---
# minimap2 — 33× PacBio HiFi across a chromosome, checked against the GIAB truth set

Aligns 2.16 Gbp of NA12878 HiFi reads to chr20 on Graviton in 4½ minutes, then verifies the placement recovers 99.96% of GIAB's known SNVs. For anyone aligning long reads.

> **Long reads are cheap to align.** 2.16 Gbp at 7.8 Mbp/s for $0.064 — the whole four-task path, prep to truth check, costs **$0.10**.

## Run it

```bash
make stage RECIPE=minimap2   # once: pull chr20 HiFi reads out of the published GIAB BAM
make run   RECIPE=minimap2   # align (~4.5 min) → sort → truth-support check
make ls    RECIPE=minimap2   # aln.sam.gz, hifi.sorted.bam + .bai, three check files

minimap2 -ax map-hifi -t 16 --MD chr20.fa hifi.chr20.fq.gz > aln.sam
```

## What it costs

| step | tool | wall | box | $ |
|---|---|---|---|---|
| prep — chr20 reads out of the GIAB BAM | samtools | 209 s | `m8g.2xlarge` | 0.0208 |
| **align 2.16 Gbp** | **minimap2** | **276 s** | `c8g.4xlarge` | **0.0640** |
| sort + index | samtools | 20 s | `m8g.2xlarge` | 0.0090 |
| truth-support check | bcftools | 17 s | `m8g.2xlarge` | 0.0074 |

**7.8 Mbp/s on 16 Graviton4 cores.** For scale, the short-read recipe spends [$0.15 on 4.83 Gbp](../bwa-samtools/README.md) — per base the two are within 2×, so alignment is not the expensive part of a long-read pipeline.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| PacBio HiFi, `-ax map-hifi` | ONT: `-ax map-ont`; older CLR: `-ax map-pb` | the preset is the whole configuration — mismatching it to the chemistry is the most common long-read error, and it degrades quietly. |
| chr20 at 33× | your reads + reference | minimap2 indexes on the fly; no separate index step and no index to stage. |
| `-t 16` | your core count | minimap2 threads well; the data path, not the chip, is usually the limit. |

**Leave the workload** — a real HiFi library at production depth with a published truth set to check placement against. **Scale it** to a whole genome by swapping the reference; there is no index build to budget for, unlike [bwa-mem2](../bwa-mem2/README.md).

<details>
<summary>As shipped: the checks, why the identity floor is 98 and not 99, the reads, pins</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| primary records | **exactly 217,281** — one per input read | **217,281** |
| records off chr20 | 0 | **0** |
| mapped | ≥ 95% | **97.459%** |
| mean identity vs GRCh38 | ≥ 98% | **98.7056%** |
| BAM magic | `1f8b0804` | `1f8b0804` |
| `samtools quickcheck` | EOF block present | passes |
| mean depth / chr20 covered | ≥ 25× / ≥ 98% | **32.37× / 98.60%** |
| **GIAB truth SNVs recovered** | **≥ 0.95** | **0.99961** (69,210 of 69,237) |

One primary record per input read is a conservation identity — minimap2 must account for every
read exactly once, mapped or not — and it costs nothing to check. The sorted BAM holds 271,744
records against 217,281 primaries; the difference is supplementary alignments for reads minimap2
split, which is expected for long reads and why the identity is stated on primaries.

### Why the identity floor is 98%, not 99%

PacBio HiFi is advertised at Q20 — 99% read accuracy — and asserting that here would be wrong.
**Alignment identity against GRCh38 is a different quantity**: it also absorbs genuine
NA12878-versus-reference variation (~1 per 1,000 bp) and indel representation, so 98.71% is what a
correct HiFi alignment looks like, not a shortfall. 98% is the bar that separates it from wrong
data or a broken index — ONT R9 sits near 92–95%, Illumina near 99.5% — with real margin.

Same reasoning for mapping rate. The reads were extracted from chr20 *alignments*, so the naive
expectation is ~100% mapping back; 97.46% is what actually happens, because the reads pbmm2 placed
on chr20 include repeat-rich and pericentromeric ones that a different aligner's thresholds
decline to place confidently. 95% is the floor; a wrong reference lands far below.

### The truth-support check, and what it is not

At each of the 69,237 GIAB truth SNV sites inside the high-confidence BED, the long-read pileup is
genotyped and compared to the truth allele: **69,210 recovered, 27 missed, recall 0.99961**. That
is a statement about *placement* — if minimap2 put reads in the wrong place, the known variants
would stop being visible.

**Do not read it against [the caller table](../../measurements/callers-real/README.md).** Those
numbers come from genome-wide discovery, where a caller must also avoid false positives; this one
genotypes only where the answer already is, which is a much easier task and cannot measure
precision at all. Comparing the two would be comparing a placement check to a calling benchmark.

### The reads

`HG001_GRCh38.haplotag.RTG.trio.bam` is 58.4 GB of Sequel II CCS HiFi aligned to GRCh38; the prep
task pulls chr20 out of it over https and converts back to FASTQ, so minimap2 performs a genuine
alignment rather than inheriting pbmm2's placements. `-F 0x900` drops secondary and supplementary
records so each read appears exactly once. Measured: **217,281 reads, 2,159,335,308 bases, mean
9,938 bp, 33.5× on chr20** — consistent with a library size-selected at ~9–11 kb.

Counting those bases needs care at this scale: awk's `%d` is 32-bit here, so 2.16 Gbp silently
clamps to `INT32_MAX` (2,147,483,647) and quietly corrupts any mean derived from it. The spec uses
`%.0f`, which goes through a double and is exact past 9e15.

### Short-read mode, kept as a method check

`04-shortread-crosscheck.task.json` runs `-ax sr` on a small paired fixture and compares placements
with bwa. It is a *mode* demonstration on a deliberately small input, not a workload: naive
all-mapped concordance is 0.43 on a repeat-heavy subsample, and 0.9921 once gated on MAPQ ≥ 30 and
≤ 5 bp — the worked example behind [compare like with like](../../practices/cross-checks.md).

### Pins

| | data tier |
|---|---|
| minimap2 | `quay.io/aarchbio/minimap2@sha256:ef4a5fb7…` (2.31-r1302, `linux/arm64`) |
| samtools / bcftools | `@sha256:11917396…` / `@sha256:8171fe74…` |
| reads | `s3://giab/data/NA12878/PacBio_SequelII_CCS_11kb/HG001_GRCh38/` — published GIAB deposit |
| truth | `s3://giab/release/NA12878_HG001/NISTv4.2.1/GRCh38/` — benchmark VCF + high-confidence BED |
| reference | `chr20.fa` staged by [bwa-samtools](../bwa-samtools/README.md) |

The reference, truth VCF and BED are the same objects the [caller recipes](../gatk4/README.md)
read, so long- and short-read results are anchored to identical bytes.

### Run + verify

```sh
make run RECIPE=minimap2
make ls  RECIPE=minimap2
```

Expect `smoke-check.txt` with `primary_records 217281` and `mean_identity 98.7056`, and
`truth-support.txt` with `truth_snv_recall 0.99961`.

</details>
