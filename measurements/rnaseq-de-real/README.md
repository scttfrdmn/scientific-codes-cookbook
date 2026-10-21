# DESeq2 and limma-voom on a real study, Graviton4, Intel and AMD

> **The whole airway study reproduces Bioconductor's published DESeq2 workflow on all three chips:
> every count it reports and all 60 values it prints, to the last digit.** The study costs
> **$0.007 billed on Graviton4, $0.009 on Intel and $0.010 on AMD**. The statistics take 20 s of the 332 s
> billed; the rest is boot, Docker and the image pull.

Workload: **GSE52778** (Himes et al. 2014), four primary airway smooth-muscle cell lines, each
untreated and dexamethasone-treated, so 8 samples in a paired `~ cell + dex` design across the whole
transcriptome. Two count matrices of that experiment ship in Bioconductor's `airway` 1.30.0:
`gse` (tximeta/salmon, 58,294 genes) and `airway` (summarizeOverlaps integer counts, 63,677 genes).

**Why this size is the real one:** a 4-vs-4 paired design over every gene is the typical bulk
RNA-seq DE study, and it is the study Bioconductor's own reference workflow uses. Genes are the
axis that matters, and all of them are here. DESeq2's cost grows with sample count; a 100+-sample
cohort is where box choice would start to matter, and that has not been measured.

## Run it

```sh
export AWS_PROFILE=aws
bash stage-inputs.sh "$COOKBOOK_BUCKET"
for s in $(python3 ../../scripts/spec_arch.py rnaseq-de arm64 "$COOKBOOK_BUCKET" "$(date +%s)" \
             01-deseq2.task.json 02-compare.task.json); do spawn task run --spec "$s" --wait; done
# x86: the same line with x86_64 (Intel) or amd in place of arm64; outputs land under measurements-x86_64/ or measurements-amd/
```

Task 1 (DESeq2 image) runs [`deseq2.R`](deseq2.R). Task 2 (edgeR image, which also ships limma)
runs [`voom.R`](voom.R) on the matrix task 1 wrote. Both scripts are staged as pinned files and
checked by sha256 before they run.

## Results

2 vCPU / 4 GiB on every chip, same versions, same input bytes. DESeq2 and limma are single-threaded.

| chip | instance | tool wall (DESeq2 + limma/edgeR) | billed (2 tasks) | peak RSS | $/hr | compute $/study | billed $/study |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.large` | 16.2 + 3.8 = **20.0 s** | 167 + 165 = 332 s | 1.04 GB | 0.0798 | 0.00044 | **0.0074** |
| Intel (same gen) | `c8i.large` | 15.2 + 3.4 = 18.6 s | 166 + 166 = 332 s | 1.07 GB | 0.0937 | 0.00048 | **0.0086** |
| AMD EPYC 9R45 | `c8a.large` | 12.9 + 2.7 = **15.5 s** | 161 + 160 = 321 s | 1.07 GB | 0.1078 | 0.00047 | **0.0096** |

- **x86 runs the statistics 7% faster, and Graviton4 is still cheaper per study**: 8% on compute,
  15% billed, because the hour costs 17% less. At n = 1 per cell, a 7% wall difference on a
  20-second run is not a performance claim. The two images are also different builds: R 4.5.3 with
  OpenBLAS 0.3.34 on arm64, against R 4.5.2 with OpenBLAS 0.3.30 (DESeq2 image) and 0.3.33 (edgeR
  image) on x86.
- **AMD runs the statistics fastest (15.5 s, 1 thread per core, OpenBLAS `Cooperlake` kernel) and
  bills most**, because its hour is the dearest: compute $/study is a wash with Intel, billed is 12%
  above it and 30% above Graviton4. Same n = 1 caveat.
- **94% of the bill is overhead.** Per task: about 13 s of boot, 40 s of Docker install, 45 s of
  image pull, and 40–60 s between the end of the task and termination. Two tasks pay it twice. More cores or a newer chip cannot reduce it. The smallest box
  that fits is the right one. Peak RSS is 1 GB, so `c8g.large` has 4x headroom.
- Wall time includes R startup and package loading. Inside it, DESeq2 takes 5.4 s on `gse` and
  4.1 s on `airway`; voom takes 0.8 s and edgeR-QL 2.6 s.

## What is asserted, on all three chips

All of these are `stopifnot`-equivalent: a failure fails the task.

| check | kind | observed (all three chips) |
|---|---|---|
| `gse` through rnaseqGene 1.34.0, verbatim: genes kept, `padj<0.1` total / up / down, low-count, outliers, `alpha=0.05`, `lfcThreshold=1` | published reference | **16637, 4381 / 2362 / 2019, 646, 0, 3602, 240**, all equal to the workflow's |
| the 10 rows the workflow prints from `res`, all 6 columns | published reference | **60 of 60** agree to every printed digit |
| `airway` size factors vs an independent median-of-ratios in ratio space (no logs) | identity | max relative diff **3.2e-16**, asserted <= 64 eps |
| direction on genes DESeq2 and limma-voom both call at FDR < 0.05 | cross-code, exact | **4047 of 4047** |
| each method's top 100 genes by p-value called by the other at FDR < 0.05 | cross-code | **100 / 100** both ways |
| direction on genes all three methods call | cross-code, exact | **4019 of 4019** |
| the 7 genes Himes et al. name (DUSP1, KLF15, PER1, TSC22D3, C7, CCDC69, CRISPLD2) DE in all three; CRISPLD2 up | published biology | **7 of 7**; CRISPLD2 log2FC 2.627 / 2.625 / 2.623 |

**The published reference is version-matched.** rnaseqGene 1.34.0 is the Bioconductor 3.22
workflow, built with DESeq2 1.50.0 and airway 1.30.0. The image ships DESeq2 1.50.2, a patch
release, and the run reproduced the workflow's numbers to every printed digit.

**Why size factors are compared on `airway`, not `gse`:** DESeq2 imports `gse` with tximeta's
average transcript lengths, so it uses per-gene normalization factors, not size factors.
**Why the convention matters in the recompute:** 16,072 genes enter, an even number, so the median
falls between two ratios. DESeq2 takes the median in log space, which is their geometric mean. The
textbook median is their arithmetic mean. The assertion uses the geometric mean. The arithmetic one
is reported: it differs by up to 2.1e-9, inside its AM-GM bound `(hi - lo)^2 / (8 lo)`.

**Why the cross-code metric is set agreement and direction, not a tolerance on values:** the two
methods use different models (a negative-binomial GLM with Wald tests against a log-CPM linear model
with precision weights) and different normalizations (median-of-ratios against TMM). Their p-values
are on different scales: DESeq2's smallest `padj` here is 1e-124 and limma's is 2e-6, for the same
gene. So no raw-value tolerance could be justified. On the genes where the evidence is overwhelming,
both must agree on the answer. A top-100 gene is in the top 0.6% of 16,139 tested. The limits of
that agreement are reported but not asserted: top-100 overlap is 84 (DESeq2 vs limma) and 77
(DESeq2 vs edgeR); Spearman on log2FC is 0.9968 / 0.9971 / 0.9994; Spearman on p-value rank is
0.9893. These numbers measure how the models differ on borderline genes as much as they measure
agreement, which is why they are not used as bands. Both methods run on the same 16,139 genes, the
same filter (>= 10 counts in >= 4 samples) and the same design, so that inputs and modes match.

## What differed across chips: fixed points, to their convergence tolerance

Every asserted number and every significance call is identical on all three chips: **0 genes flip** at
`padj` 0.05 or 0.1, on either matrix. The input matrix and the size factors are **bit-identical**.
The full-precision tables differ in the last digits, and how much they differ depends on the method:

| quantity | max relative difference, arm64 vs x86 | why |
|---|---|---|
| limma-voom log2FC / p | 7.6e-12 / 1.8e-13 | closed-form least squares: rounding only |
| edgeR-QL log2FC / p | 3.7e-11 / 9.4e-10 | iterated GLM fit, tight tolerance |
| DESeq2 log2FC | 1.3e-7 (`gse`), 1.9e-8 (`airway`) | dispersion and GLM fits stop at a convergence tolerance |
| DESeq2 p-value | 8.8e-4 at p = 2.5e-111; <= 4.7e-5 where p > 1e-6 | far-tail amplification of the same tiny change in the Wald statistic |

These are fixed points reached through different floating-point paths (different CPU, compiler and
OpenBLAS). They agree to the precision their solvers converge to, and no further. That is why the
recipe asserts counts, directions and published digits, and never a full-precision p-value. On the
recipe's 2,000-gene synthetic fixture, the DESeq2 difference was 2e-12. A real matrix has more genes
that need many iterations, so the numerical noise floor is set by the data.

**AMD against Intel: identical to the last digit.** Every column of all three full-precision tables
(DESeq2 on `gse` and `airway`, and the three-method table) is byte-for-byte the same on `c8a` and
`c8i`: same x86 images, and here the two x86 cores take the same floating-point path. So AMD against
Graviton4 is exactly the Intel-against-Graviton4 table above. On AMD both images' OpenBLAS
(0.3.30, 0.3.33: no Zen 5 target) select the `Cooperlake` kernel; the Intel run's kernel was not
recorded.

## Pins

| | |
|---|---|
| data | Bioconductor `airway_1.30.0.tar.gz`, sha256 `78f2179b25983313d5450876236f92b58c10d0845d62b7a832329e32c871cad1` (versioned release; [stage-inputs.sh](stage-inputs.sh)) |
| published reference | rnaseqGene 1.34.0 vignette, `bioconductor.org/packages/3.22/workflows/vignettes/rnaseqGene/inst/doc/rnaseqGene.html`; Himes et al. 2014, *PLoS One* 9:e99625 |
| arm64 images | `quay.io/aarchbio/bioconductor-deseq2@sha256:6d87efaf…`, `quay.io/aarchbio/bioconductor-edger@sha256:09169b35…` |
| x86 images | `quay.io/biocontainers/bioconductor-deseq2@sha256:de4543b7…`, `quay.io/biocontainers/bioconductor-edger@sha256:6b2154b1…` |
| raw reports | [results/](results/): each task's `report.tsv`, per chip; `amd/host.txt` is the AMD box's CPU, SMT and OpenBLAS core |

n = 1 per cell. All three methods are deterministic given the matrix, so the asserted results
are the same on any re-run. The timings are single samples.

**Spend:** the AMD leg (two tasks, both first-attempt green) billed **$0.0096**. It was launched with
spawn 0.131.1 built from its release tag, not the installed 0.139.1, whose own wrapper leaves too
little of EC2's 16 KiB user data for this spec (spawn#1246); the spec bytes are the ones the other two
chips ran, plus one non-fatal line that writes `host.txt` (`/proc/cpuinfo`, the SMT siblings, and
`OPENBLAS_VERBOSE=2`) before the task's own script starts.
