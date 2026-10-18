---
tool: minimap2
tool_version: 2.31-r1302
image: quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a
images:
  - quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a
  - quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7
  - quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e
last_verified_x86_64: 2026-10-13
images_x86_64:
  - quay.io/biocontainers/bcftools@sha256:186c45de3059dbb1837006c27788284c89d17f8e693e7804624b6cf6ca5caca0
  - quay.io/biocontainers/minimap2@sha256:f7e4a9a17912eea542500988ee96013e37a887b3746673636f2773cc81052590
  - quay.io/biocontainers/samtools@sha256:a130447589651ed09252aa95a5e4f4132942cdb54d835d81a04a9a930d656561
spawn_version: 0.111.4
last_verified: 2026-10-13
---
# minimap2 — 33× PacBio HiFi across a chromosome, checked against the GIAB truth set

Aligns 2.16 Gbp of NA12878 HiFi reads to chr20 on Graviton in 4½ minutes, then verifies the placement recovers 99.96% of GIAB's known SNVs. For anyone aligning long reads.

## Run it

```bash
make stage RECIPE=minimap2   # once: pull chr20 HiFi reads out of the published GIAB BAM
for s in $(make -s spec RECIPE=minimap2); do spawn task run --spec "$s" --wait; done   # align (~4.5 min) → sort → truth-support check
make ls    RECIPE=minimap2   # aln.sam.gz, hifi.sorted.bam + .bai, three check files

minimap2 -ax map-hifi -t 16 --MD chr20.fa hifi.chr20.fq.gz > aln.sam
```

## What it costs

The chr20 path is prep 209 s on `m8g.2xlarge` ($0.021), **align 276 s on `c8g.4xlarge` ($0.064, 7.8 Mbp/s on 16 Graviton4 cores)**, then sort 20 s and truth check 17 s ($0.016): **$0.10** for the four tasks. The short-read recipe spends [$0.15 on 4.83 Gbp](../bwa-samtools/README.md). Per base the two are within 2×, so alignment is not the expensive part of a long-read pipeline.

## Which box — [measured](../../measurements/minimap2-real/README.md) (the whole 30× genome, 90.5 Gbp vs all of GRCh38, 32 threads)

| chip | instance | align wall | $/hr | **compute $/result** | billed $/result |
|---|---|---|---|---|---|
| Graviton4 | `r8g.8xlarge` | 2,618 s | 1.8851 | 1.388 | **2.095** |
| Xeon 6975P-C | `r8i.8xlarge` | 2,880 s | 2.2227 | 1.802 | 3.119 |
| **EPYC 9R45** | `r8a.8xlarge` | **1,774 s** | 2.5562 | **1.280** | 2.532 |

**For the alignment itself, take AMD:** it is 1.48× faster than Graviton4 and 8% cheaper per genome. The whole-genome rate is 35 Mbp/s on Graviton4 and varies 3× between chromosomes, so the chr20 timing above does not scale linearly. Choose `r8` for its RAM. minimap2 itself needs 26 GB, but staging the 75 GiB of reads needs 95 GiB of tmpfs. Every identity held on all three chips, and recall stayed at 0.99960. All alignments match apart from 9 of 11.1M records, which differ by one MAPQ or DP-score unit in the chr4 centromere.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| PacBio HiFi, `-ax map-hifi` | ONT: `-ax map-ont`; older CLR: `-ax map-pb` | the preset is the whole configuration — mismatching it to the chemistry is the most common long-read error, and it degrades quietly. |
| chr20 at 33× | your reads + reference | minimap2 indexes on the fly; no separate index step and no index to stage. |
| `-t 16` | your core count | minimap2 threads well: at 32 threads on the whole genome it kept 67–74% of them busy. The chip moved the wall 1.6× (above). |

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

### On x86_64

All five tasks on same-generation Intel (`c8i` / `m8i`), from bioconda's own x86 builds of the
same versions — `quay.io/biocontainers/minimap2@sha256:f7e4a9a1…` (2.31), `samtools@sha256:a1304475…`
(1.24) and `bcftools@sha256:186c45de…` (1.24), all `linux/amd64`; not cosign-signed, so the digest
is the pin. The reads were re-extracted from the GIAB deposit on x86, and the short-read check
reads the x86 bwa-samtools baseline (byte-identical to arm64's):

| | arm64 | x86_64 |
|---|---|---|
| reads / bases extracted | 217281 / 2159335308 | 217281 / 2159335308 |
| primary / pct mapped / identity | 217281 / 97.459 / 98.7056% | 217281 / 97.459 / 98.7056% |
| truth SNV recall (TP / FN) | 0.99961 (69210 / 27) | 0.99961 (69210 / 27) |
| short-read concordance vs bwa | 0.9921 | 0.9921 |
| `hifi.sorted.bam` + `.bai`, `sr-mm.sam`, `sr-smoke-check.txt` | — | **byte-identical** to arm64 |

The alignment is the same, bit for bit, at 16 threads. The two gzipped intermediates differ in
size (`hifi.chr20.fq.gz` 1.81 vs 1.37 GB, `aln.sam.gz` 1.89 vs 1.19 GB) only because the
compressor does — GNU gzip 1.12 in the ARM images, BusyBox gzip in bioconda's x86 ones; the
sorted BAM built from them is identical. The logs and check files differ only in wall times.

### Run + verify

```sh
make run RECIPE=minimap2                  # Graviton4
make run RECIPE=minimap2 ARCH=x86_64      # same specs, x86 pins, writes runs-x86_64/
make ls  RECIPE=minimap2
```

Expect `smoke-check.txt` with `primary_records 217281` and `mean_identity 98.7056`, and
`truth-support.txt` with `truth_snv_recall 0.99961`.

</details>
