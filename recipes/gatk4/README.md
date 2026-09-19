---
tool: gatk4
tool_version: 4.6.2.0
image: quay.io/aarchbio/gatk4@sha256:92065598dde922a2223eb57863606c4bf8558b11af938ada9637efb9d0d8d0fc
spawn_version: 0.104.0
last_verified: 2026-09-12
---
# GATK4 — HaplotypeCaller, cross-checked against bcftools and freebayes

GATK4's HaplotypeCaller calls variants on the shared 30× fixture, then a three-way concordance shows it agrees with bcftools and freebayes on identical bytes. For anyone whose pipeline runs GATK and wants it on Graviton.

## Run it

```bash
# HaplotypeCaller needs the reference indexed (.fai + .dict) beside chr20.fa
gatk HaplotypeCaller -R chr20.fa -I HG00096.chr20_2.0-2.4Mb.30x.bam \
  -L chr20:2000000-2400000 --native-pair-hmm-threads 1 -O gatk.vcf.gz
```

Two tasks — `gatk` calls in one image, then `bcftools` does the three-way concordance in the next, the VCFs handed between them through S3, because aarch.bio ships [one tool per image](../../practices/container-path.md).

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| the 30× `chr20:2,000,000-2,400,000` fixture | your BAM + region | **shared with [bcftools](../bcftools/README.md)/[freebayes](../freebayes/README.md)** so the three callers compare on identical bytes; swap all three together or the cross-check is meaningless. |
| single-sample direct calling | GVCF + joint genotyping (`-ERC GVCF` → `GenotypeGVCFs`) | the production path for cohorts; this proves the caller runs and agrees, not a joint-calling workflow. |
| `--native-pair-hmm-threads 1` | your core count | pinned to 1 for a legible run; PairHMM is the hot loop, so scale it — but see the arm64 caveat below. |

**Leave the fixture** to prove GATK runs and agrees with two independent callers; it's the same 30× region where confident concordance is a real check ([why 30×](../bcftools/README.md)). **Scale it** to a whole genome and GVCF joint-calling for real cohort work — a different workflow, not a bigger fixture.

## Shape, size, cost

Two tasks, `c8g.large` (2 vCPU / 4 GiB), TTL 5m/4m, cap $0.03/$0.02. HaplotypeCaller's compute is ~**9 s** on the 400 kb region; the recorded `gatk4-call` window was **2m22s**, the Java image pull dominating. **These timings are not compute cost.**

**Sizing + the arm64 caveat, stated honestly:** GATK4 runs on Graviton, but its bundled **Intel GKL native libraries are x86-64**, so the AVX-accelerated PairHMM can't load and GATK falls back to the Java `LOGLESS_CACHING` path — GATK's own log calls it "MUCH slower." That's a *missing native acceleration on arm64*, not a broken build: correctness is identical, but a reader comparing wall time against an x86 cluster should expect the PairHMM step to be slower here. Size a real cohort by genome size and sample count, not this region.

<details>
<summary>As shipped: the three-way cross-check, GATK's own checks, pins, smoke checks, run + verify</summary>

### The three-way cross-check (the identity that earns the recipe)

GATK4, bcftools and freebayes are three independent callers with different models (local re-assembly vs pileup vs haplotype), so they do **not** agree exactly and a raw VCF diff would fail by design. Normalised to **confident SNVs** (QUAL≥20, split multiallelics, left-aligned, compared on `POS:REF:ALT`) on the **same 30× bytes**, they agree strongly: gatk↔bcftools **0.9704**, gatk↔freebayes **0.9100**, and **605 of 675** union SNVs (**0.8963**) are called by all three. Three independent callers agreeing in the confident set is a stronger statement than the pair's own [0.9103](../bcftools/README.md) — the same [compare like with like](../../practices/cross-checks.md) discipline, one caller more. The floors are what independent callers should reach at 30×, not the observed values shaved. Every Jaccard reproduced to four figures on Graviton (the verifying run), with bcftools↔freebayes hitting 0.9103 a third time — across two machines and the pair's own recipe — so the agreement is deterministic, not coincidentally close. (This recipe is standalone; the concordance *points at* the existing pair, the way [megahit](../megahit/README.md) stands beside spades rather than merging with it.)

### GATK's own checks (task 1, so a bad call fails before the comparison)

A valid single-sample VCF, all records on chr20, a plausible variant/SNV count — and the arm64 PairHMM fallback asserted from the log (`LOGLESS_CACHING`), which doubles as proof the run is genuinely on AArch64 and not an emulated x86 image.

### Pins (data tier: derived-from-immutable + in-image index)

| thing | pin |
|---|---|
| gatk4 image | `quay.io/aarchbio/gatk4@sha256:92065598…` (`4.6.2.0`, cosign-signed, `linux/arm64`) |
| bcftools image (concordance) | `quay.io/aarchbio/bcftools@sha256:8171fe74…` (`1.x`) |
| 30× fixture | `HG00096.chr20_2.0-2.4Mb.30x.bam` sha256 `6949939b…` (staged by [bcftools](../bcftools/README.md)) |
| reference | `chr20.fa` (staged by [bwa-samtools](../bwa-samtools/README.md)); `.fai` sha256 `295950bb…`, `.dict` sha256 `b9e597b7…` (this recipe's stage) |

The reference index is built once at `make stage` with samtools (the gatk4 image has no samtools); the `.dict` URI is fixed with `-u` so it pins. The task re-verifies the BAM's sha256 on the box.

### Smoke checks (inside the tasks; measured before launch)

**Task 1 — call** · variants_total 802 (700–900) · snv_count 661 (560–760) · all_on_chr20 0 · sample_column 1 · arm64_java_pairhmm ≥1
**Task 2 — concordance** · gatk↔bcftools 0.9704 (≥0.90) · gatk↔freebayes 0.9100 (≥0.85) · bcftools↔freebayes 0.9103 (≥0.85) · three_way 0.8963 (≥0.82)

### Run + verify (the full sequence — GATK's concordance reads the pair's VCFs)

```sh
make stage RECIPE=bwa-samtools   # chr20.fa (shared reference)
make stage RECIPE=bcftools       # the 30x fixture (shared)
make stage RECIPE=gatk4          # chr20.fa.fai + chr20.dict (GATK needs them)
make run   RECIPE=freebayes      # produces freebayes.vcf
make run   RECIPE=bcftools       # produces bcftools.vcf.gz (+ the 2-way concordance)
make run   RECIPE=gatk4          # 01-call then 02-concordance, the three-way
make ls    RECIPE=gatk4          # gatk.vcf.gz, smoke-check.txt, concordance.txt
```

The smoke checks run inside the tasks; the bucket listing is the second half ([exit 0 isn't proof](../../practices/container-path.md)). Re-run: `make run` launches fresh tasks and overwrites this prefix — no spec edit needed.

**Fan out across samples.** One call is one task; a cohort is the same task as a [job array](../../patterns/job-arrays.md) — validate on one sample with `make run` above, *then* fan out one instance per sample, each keyed by `$JOB_ARRAY_INDEX`. `spawn array status` / `collect` / `retry --failed` manage the set; add `--max-concurrent-auto` when a shared reference or spot capacity pushes back.

</details>
