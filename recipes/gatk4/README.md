---
tool: gatk4
tool_version: 4.6.2.0
image: quay.io/aarchbio/gatk4@sha256:92065598dde922a2223eb57863606c4bf8558b11af938ada9637efb9d0d8d0fc
spawn_version: 0.111.4
last_verified: 2026-09-26
---
# GATK4 — HaplotypeCaller on a 36× genome, scored against the GIAB truth set

Calls variants across the whole of chr20 in NA12878 at 36× on Graviton, then measures precision and recall against NIST's published benchmark. For anyone running GATK and choosing what to rent.

> **Cores do nothing here.** GATK's PairHMM and SmithWaterman accelerators are x86-64 native libraries, so on Graviton both fall back to Java and run single-threaded whatever you pass. Buy the newest generation, not more cores, and parallelise by scattering intervals.

## Run it

```bash
make stage RECIPE=gatk4   # reference index, GIAB truth slice, and chr20 out of the 30x CRAM
for s in $(make -s spec RECIPE=gatk4); do spawn task run --spec "$s" --wait; done   # call (~2h 12m) then score against GIAB
make ls    RECIPE=gatk4   # gatk.vcf.gz + smoke-check.txt + concordance.txt

gatk HaplotypeCaller -R chr20.fa -I NA12878.chr20.30x.bam -L chr20 \
  --native-pair-hmm-threads 1 -O gatk.vcf.gz
```

## Which box — measured (same image, same bytes, one thread)

| generation | instance | wall | $/hr | **compute $/result** | billed $/result |
|---|---|---|---|---|---|
| Graviton2 | `c6g.xlarge` | 121 s | 0.1360 | 0.004572 | 0.008500 |
| Graviton3 | `c7g.xlarge` | 95 s | 0.1450 | 0.003826 | 0.007049 |
| Graviton4 | `c8g.xlarge` | 79 s | 0.1595 | 0.003501 | 0.006691 |
| **Graviton5** | `c9g.xlarge` | **62 s** | 0.1739 | **0.002994** | **0.005989** |

**Take the newest generation and the smallest size that holds the heap.** Graviton2→5 is **1.95× faster and 34.5% cheaper per result** despite a 27.9% higher rate card, and all four returned byte-identical variant counts. The rows are a 2 Mb interval so four generations were affordable; the whole chromosome is one run, below. Adding cores buys **nothing** — 77 s at one thread, 78 s at eight.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| NA12878 chr20 at 36× | your BAM + `-L` interval | needs a read group (`SM:`) and `SO:coordinate`, and the reference dictionary must match the BAM's contigs — the two things that reject most BAMs. |
| whole chr20 in one task | one interval per task | the **only** parallelism on Graviton: GATK's own scatter-gather as a [job array](../../patterns/job-arrays.md), then `bcftools concat`. |
| no variant filtering | hard filters or VQSR | precision is 0.990 *because* nothing is filtered; filters raise it materially and are what production ships. |

**Leave the workload** — a real sample at production depth over a whole chromosome, scored against a published truth set, so the accuracy transfers to your data. **Scale it** by scattering intervals across tasks, never by growing the box; for a cohort, swap direct calling for `-ERC GVCF` → `GenotypeGVCFs`.

## Shape, size, cost

Whole chr20 on `c8g.xlarge`: **7,929 s of HaplotypeCaller inside a 7,982 s billed window** — only 53 s of overhead, so this run is **99.3% compute** and $0.354 of Graviton time. That is the opposite of [salmon](../salmon/README.md), where 40% of the bill is not the tool.

<details>
<summary>As shipped: the GIAB accuracy numbers, why cores are inert, the BAM that GATK rejects, pins</summary>

### The checks

Structural, in task 1 — a VCF that is single-sample, entirely on chr20, indexed, and produced by
a genuinely AArch64 GATK:

| observable | assertion | observed |
|---|---|---|
| sample | `NA12878` | **NA12878** |
| records off chr20 | 0 | **0** |
| sample columns | 1 | **1** |
| VCF index written | yes | **yes** |
| arm64 Java PairHMM | ≥1 `LOGLESS_CACHING` | **1** |
| variants / SNVs | recorded | **131,730 / 109,467** |

### Accuracy against a published truth set — the identity that earns the recipe

NA12878 is **GIAB HG001**, so NIST publishes both its benchmark variants and the BED of regions
where that benchmark is confident. That turns "produce a plausible VCF" into "reproduce a
published accuracy", which is stronger than any agreement between our own tools
([why](../../practices/cross-checks.md)).

Both sides restricted to the **56,000,154 high-confidence bases** of chr20, normalised
identically (split multiallelics, left-aligned against the same `chr20.fa`), SNVs at QUAL≥30:

| | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|
| **SNVs** (asserted) | 68,895 | 696 | 315 | **0.99000** | **0.99545** | **0.99272** |
| indels (observation) | — | — | — | 0.99419 | 0.99372 | 0.99395 |

`TP + FN = 69,210` is the truth-side SNV count inside the BED — the same number a local identity
test (the truth set compared against itself, which scores exactly 1.00000) reports, so both sides
use the same denominator.

**Floors are 0.98 precision / 0.99 recall / 0.985 F1, and the precision floor is deliberately
looser than the observed value.** Precision is 0.990 because this recipe applies **no** variant
filtering — no VQSR, no hard filters — so low-quality calls survive as FPs. That is a property of
the recipe, not of GATK. A 0.99 floor would sit 0.001 from the observed value and fail on noise,
which teaches people to ignore failures; 0.98 is the bar a correct run clears with room, and
below it the reference, sample or depth is wrong.

**Indels are reported, not asserted.** `POS:REF:ALT` equality after left-alignment still counts
two correct spellings of one indel as FP *and* FN, which `hap.py`'s haplotype comparison would
credit. Asserting it would be asserting a representation difference, not accuracy — the
[compare like with like](../../practices/cross-checks.md) rule.

### Why cores are inert, with the message that misleads

```text
Unable to load libgkl_compression.so from native/libgkl_compression.so
  (…cannot open shared object file: No such file or directory
   (Possible cause: can't load AMD 64 .so on a AARCH64 platform))
SmithWatermanAligner - AVX accelerated SmithWaterman implementation is not supported,
  falling back to the Java implementation
```

It reads like a missing file and is really an architecture mismatch. Correctness is unaffected —
the Java PairHMM computes the same likelihoods, and all four generations agreed exactly — but
`--native-pair-hmm-threads` controls only the *native* implementation, so it does nothing. Hence
one thread, pinned, and scatter-gather for parallelism. Full measurement:
[measurements/gatk4-real](../../measurements/gatk4-real/README.md).

### The BAM GATK rejects, and why prep is its own task

HaplotypeCaller needs a read group, `SO:coordinate`, and a reference dictionary matching the
reads' contigs. The published CRAM has the first two; the third is the work:

- its header carries **3,366 contigs** and this reference is chr20 alone, so the header must be
  restricted — and **`samtools reheader` cannot do it.** A BAM record stores a *numeric* index
  into the `@SQ` list, so dropping lines leaves every read pointing at the wrong contig;
  `samtools index` then fails with `Numerical result out of range`. SAM text stores the contig
  *name*, so a text round-trip re-resolves them.
- a chr20-only reference decodes a whole-genome CRAM only because the M5 matches:
  `b18e6c531b0bd70e949a7fc20859cb01`, verified equal on both sides.

Prep runs on the box, not locally, because it reads ~400 MB of CRAM slices and writes a 921 MB
BAM. Measured: **17,705,654 records, mean depth 36.33×, 99.05% of chr20 covered.**

### Pins

| | data tier |
|---|---|
| gatk4 | `quay.io/aarchbio/gatk4@sha256:92065598…` (4.6.2.0, cosign-signed, `linux/arm64`) |
| bcftools (scoring) | `quay.io/aarchbio/bcftools@sha256:8171fe74…` |
| reads | `s3://1000genomes/1000G_2504_high_coverage/data/ERR3239334/NA12878.final.cram` — published NYGC 30× |
| truth | `s3://giab/release/NA12878_HG001/NISTv4.2.1/GRCh38/` — benchmark VCF + high-confidence BED |
| reference | `chr20.fa` staged by [bwa-samtools](../bwa-samtools/README.md); `.fai`/`.dict` here |

The truth slice is a region query on a versioned release, so it is derived-but-deterministic:
what is pinned is the release plus asserted content — **82,818 records, 71,316 SNVs, sample
HG001, 13,529 BED intervals, 56,000,154 bases**. The BAM is pinned by sha256
`0ad228c159e7f3b060d3476226f27b016b051f03eb73003f8929c5f954e1e02c`.

### Run + verify

```sh
make run RECIPE=gatk4
make ls  RECIPE=gatk4
```

Expect `smoke-check.txt` with `sample NA12878` and 131,730 variants, and `concordance.txt` with
`snv_precision 0.99000`, `snv_recall 0.99545`, `snv_f1 0.99272`. The smoke checks run inside the
tasks; the bucket listing is the second half, because [stage-out happens even when a command
fails](../../practices/container-path.md).

</details>
