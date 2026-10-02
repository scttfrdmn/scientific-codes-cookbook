---
tool: deseq2-edger-limma
tool_version: "DESeq2 1.50.2 / edgeR 4.8.2 / limma 3.66.0"
images:
  - quay.io/aarchbio/bioconductor-deseq2@sha256:6d87efaf6ff4691c2adfd1416c2a40444709d870408ca0ef9cced378cff042a3
  - quay.io/aarchbio/bioconductor-edger@sha256:09169b359eedcda2bd9f83366d993c2024dc40ac15441cdf68cf34f32befdd02
spawn_version: 0.111.1
last_verified: 2026-09-20
---
# RNA-seq differential expression — three methods, one planted truth

DESeq2, edgeR and limma-voom each test the same count matrix for differential expression on Graviton4, and are checked against genes whose answer is known by construction. For anyone doing bulk RNA-seq who wants to see the three standard methods agree.

> **What this covers.** 2000 genes, 4+4 samples, 200 genes planted at |log2FC| = 2 in a negative-binomial matrix generated in-code from a fixed seed. No staged data, no network. Not a benchmark of the three methods against each other.

## Run it

```bash
for s in $(make -s spec RECIPE=rnaseq-de); do spawn task run --spec "$s" --wait; done
```

```r
dds <- DESeqDataSetFromMatrix(counts, coldata, ~ grp)   # DESeq2
res <- results(DESeq(dds))

y <- estimateDisp(calcNormFactors(DGEList(counts, group=grp)), design)   # edgeR
tt <- topTags(glmQLFTest(glmQLFit(y, design), coef=2), n=Inf)$table

v  <- voom(calcNormFactors(DGEList(counts, group=grp)), design)          # limma-voom
lt <- topTable(eBayes(lmFit(v, design)), coef=2, number=Inf)
```

Two tasks: DESeq2 builds the matrix and tests it, then edgeR and limma-voom test the same matrix and the three results are compared.

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| synthetic counts, seed 42, 200 planted genes | your own count matrix (genes × samples, integers) | the planted set is what makes this checkable — with real data you have no truth to assert against, only the three methods' agreement. |
| 4 samples per group | your design | power falls fast below 3–4; the recovery numbers below are a property of *this* effect size and n, not a general claim. |
| `~ grp`, one two-level factor | your model matrix | all three take a design matrix; a batch term or covariate changes the formula, not the shape of the recipe. |

**Leave the fixture:** a matrix with planted truth lets the recipe assert *recovery* and *direction*, which no real dataset can. **Scale it** to your counts — the code path is identical, and the three-way agreement below is the thing worth reproducing on your own data.

## Shape, size, cost

Two tasks on `c8g.xlarge` (4 vCPU / 8 GiB), TTL 15m each, caps $0.04 each. Recorded windows: **1m19s** (DESeq2 + matrix) and **2m38s** (edgeR + limma + comparison) — mostly R startup and package loading, not statistics. **These timings are not compute cost.**

<details>
<summary>As shipped: two exact checks, two justified bands, why limma runs in the edgeR image, pins, smoke check</summary>

### Two exact checks, then two bands

The strong checks here are *properties*, not values — so they survive a package upgrade, where an exact count would go flaky:

| observable | assertion | observed |
|---|---|---|
| planted direction | every planted gene's sign of log2FC recovered, **by all three methods** | **200/200, 200/200, 200/200** |
| sign agreement | on genes all three call significant, all three agree on direction | **199 of 199** |
| recovery | ≥ 195 of 200 planted genes at FDR < 0.05 | 199 (DESeq2), 197 (edgeR), 197 (limma) |
| rank concordance | Spearman on log2FC ≥ 0.95, all pairs | 1.0000 / 0.9814 / 0.9814 |

Why the last one is Spearman and not a raw-value tolerance: the three use different models and shrinkage, so raw log2FC is not expected to match — the question they can all answer is *ordering*. That is the same distinction the [cross-checks](../../practices/cross-checks.md) page draws for kallisto↔salmon, one domain over.

Why direction is exact while recovery is a band: recovery depends on statistical power, which is a property of the effect size and sample count, so a threshold is honest there. Direction does not — if three methods disagree about whether a confidently-detected gene went up or down, something is broken, and that claim needs no band.

**Determinism:** the matrix comes from a fixed seed and all three methods are deterministic given it. The Graviton4 run reproduced the local arm64 run exactly — same recovery counts, same 199/199, same Spearman to four decimals.

### Why limma runs in the edgeR image

`voom` requires edgeR's `DGEList` and `calcNormFactors`, and the limma image does not ship edgeR — so limma-voom cannot run there. It runs in the edgeR image, which ships edgeR, limma and statmod. That is a real dependency between the two packages, not a convenience shortcut around [one tool per image](../../patterns/execution-shapes.md).

### Pins (data tier: synthetic / in-code)

| | |
|---|---|
| DESeq2 | `quay.io/aarchbio/bioconductor-deseq2@sha256:6d87efaf…` (1.50.2, R 4.5.3) |
| edgeR + limma | `quay.io/aarchbio/bioconductor-edger@sha256:09169b35…` (edgeR 4.8.2, limma 3.66.0) |
| input | none — the count matrix and its planted truth are generated in-code from seed 42 |

**Two image gotchas, both recorded because they cost real debugging:** R dies with `cannot create 'R_TempDir'` unless `TMPDIR` and `HOME` point somewhere writable; and spawn's task shell does not inherit the image's `PATH`, so `/opt/conda/bin` must be exported before `Rscript` is callable.

### Run + verify

```sh
make run RECIPE=rnaseq-de
make ls  RECIPE=rnaseq-de
```

The assertions run inside the second task as `stopifnot()` calls, so a failure fails the task ([exit 0 isn't proof](../../practices/container-path.md)). Expect `smoke-check.txt` with `planted_direction 200/200` and `sign_agreement 199`.

</details>
