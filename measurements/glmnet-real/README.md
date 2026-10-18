# glmnet on the whole TCGA pan-cancer RNA-seq cohort — Graviton4, Intel and AMD

> **Two lasso fits over the 11,069-sample PanCanAtlas matrix satisfy their KKT conditions at every
> one of 100 λ on all three chips, and every chip selects the same genes at every λ.** AMD EPYC runs
> the job 1.6× faster than Graviton4 and is 17% cheaper billed. Intel is the slowest and the
> dearest. Intel and AMD return byte-identical files; Graviton4 differs from them only in the last
> bits of the coefficients.

Workload: the **complete** TCGA PanCanAtlas RNA-seq matrix (Hoadley et al. 2018, *Cell*;
EB++-adjusted RSEM, 20,531 genes × 11,069 samples, 33 cancer types, 1.88 GB) and its sample
annotation table. It is fitted in two shapes that glmnet is routinely used for on expression data:

- **A. Binomial lasso, tumour vs solid-tissue normal, the whole cohort:** n = 10,885, p = 16,335.
  `cv.glmnet` uses 10 folds grouped by patient, so a tumour and its matched normal never fall on
  opposite sides of a split.
- **B. Gaussian lasso, ESR1 from every other gene, in the BRCA tumours:** n = 1,090, p = 20,250. This
  is the single-cancer gene-signature shape, with p ≫ n.

This is not a slice. Every usable sample is in A, and every gene measured in all of a fit's samples
is in its design. p > n in both fits, which the recipe's 400 × 20 fixture cannot reach.

**Data tier: a durable public id.** Both files are GDC PanCanAtlas publication files, fetched by GDC
file UUID. RODA's `tcga-2-open` holds GDC's per-aliquot files but not this matrix (both UUIDs are
absent, checked 2026-10-18). Rebuilding the matrix from 11,069 per-sample files would be the build
tier, and it would give a quantity without the batch adjustment. `00-fetch.task.json` downloads both
files on AWS, so nothing passes through a laptop. It checks each one against the md5 in its GDC
record, and [`pins.sha256`](pins.sha256) pins them again at every stage-in.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/glmnet-real/stage-inputs.sh "$B"     # runs 00-fetch on AWS if the data is not staged
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py glmnet $a "$B" "$(date +%s)" \
    measurements/glmnet-real/real.task.json)" --wait
done
bash measurements/glmnet-real/collect.sh "$B" <scratch dir>   # results/, results/compare.txt
```

[`real.R`](real.R) is the workload and its checks. Its two user-facing calls are:

```r
cvA <- cv.glmnet(XA, yA, family = "binomial", foldid = foldA, type.measure = "class")  # A
cv1 <- cv.glmnet(X, y, foldid = foldB)                                                 # B
```

## Results

`r8g.xlarge`, `r8i.xlarge` and `r8a.xlarge` (4 vCPU, 32 GiB), the recipe's image on Graviton4 and the
`r` env's linux-64 twin on the two x86 chips, the same staged bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.xlarge` | 1,538 s | 1,727 s | 9.09 GiB | 0.2356 | 0.1007 | 0.1130 |
| Xeon 6975P-C | `r8i.xlarge` | 2,059 s | 2,147 s | 9.09 GiB | 0.2778 | 0.1589 | 0.1657 |
| **EPYC 9R45** | `r8a.xlarge` | **954 s** | **1,062 s** | 9.09 GiB | 0.3195 | **0.0847** | **0.0943** |

A result is the whole verified run: both fits, their CV, and every check below.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| read 1.88 GB + log2 | 35.4 | 23.3 | 15.4 |
| `cv.glmnet` A (11 binomial paths) | 93.1 | 137.8 | 74.7 |
| `cv.glmnet` B, run twice | 19.7 | 30.9 | 13.1 |
| A and B paths at `thresh = 1e-20` | 222.2 | 196.0 | 106.1 |
| KKT checks, default-thresh refits | 74.0 | 172.3 | 84.9 |
| ridge closed form, 2 × 5 λ at `thresh = 1e-30` | 1,030.2 | 1,409.5 | 616.7 |
| 5 permuted-y nulls | 63.5 | 88.9 | 42.5 |

- **The analysis a researcher runs takes 1.6–2.9 minutes**: the read and one CV call per fit. Two thirds of
  every run is the ridge closed-form check. Coordinate descent driven to `1e-30` at κ = 217 is slow
  on purpose, so the totals price the verification as well as the science.
- **AMD is 1.61× Graviton4 overall and fastest in every phase but the BLAS-heavy KKT checks.** Its hour is 36% dearer, so it is
  16% cheaper per result in compute and 17% billed. Intel is 1.34× *slower* than Graviton4 and 47%
  dearer per result billed. Intel's 4 vCPU are 2 cores × 2 threads; AMD's and Graviton4's are 4
  full cores.
- **glmnet's coordinate descent is single-threaded.** The cgroup averaged 1.10–1.24 busy cores, and
  only the BLAS in the checks used more. The box is sized by memory: 9.1 GiB peak plus 1.9 GB staged
  in tmpfs. An `m8g.xlarge` (16 GiB) would fit, but it was not run.
- **Overhead is 88–189 s of each bill.** Graviton4 pulls the aarch.science `r` image (46 s). The x86
  twin installs the same versions offline into a small micromamba image.
- n = 1 per chip. glmnet is deterministic here: two `cv.glmnet` runs with the same `foldid` gave
  identical curves on every chip.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.R`](real.R). A failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| 20,531 × 11,069, unique gene ids and barcodes, every sample annotated | the release | yes | yes | yes |
| A: λ_max == `max_j \|x̃_jᵀ(y−ȳ)\|/n`, rel < 1e-12 | closed form | 2.4e-15 | 1.6e-15 | 1.6e-15 |
| **A: KKT at all 100 λ ≤ p·√(2·thresh) = 2.3e-6** | **optimality** | **1.526e-11** | **1.526e-11** | **1.526e-11** |
| A: same check one λ step off must miss by ≥ 1000× | control | 4.700e-03 (2,035×) | same | same |
| A: intercept score `mean(y − p)` | same bound | 1.095e-16 | 1.089e-16 | 1.089e-16 |
| B: λ_max closed form; λ_max(α = 0.5) = λ_max/0.5 | closed form | 9.7e-16; 8.1e-16 | 3.2e-16; 1.6e-16 | same as Xeon |
| **B: KKT at all 100 λ ≤ p·√thresh = 2.0e-6** | **optimality** | **1.247e-10** | **1.247e-10** | **1.247e-10** |
| B: control, one λ step off | control | 3.924e-02 (19,380×) | same | same |
| **B: ridge vs `Xᵀ(XXᵀ/n + λI)⁻¹y/n`, 5 λ, ≤ 1e3·κ·ε = 4.8e-11** | **closed form (dual)** | **3.395e-14** | **3.397e-14** | **3.397e-14** |
| B: ridge on raw y solves with λ/sd_y (naive λ: 1.4e-3, reported) | same bound | 3.397e-14 | 3.397e-14 | 3.397e-14 |
| both paths converged at every λ, and `lambda.1se` lies on them | certified range | 100 / 100 | 100 / 100 | 100 / 100 |
| `cv.glmnet`, fixed `foldid`, run twice | determinism | identical | identical | identical |
| B: permuted-y null at `lambda.1se` selects nothing, 5 permutations | null | 0,0,0,0,0 | 0,0,0,0,0 | 0,0,0,0,0 |

These identities are the recipe's, taken to p > n, where the `lm()` and `glm()` legs no longer
exist. The p × p primal ridge would be a 20k-square solve, so the closed form goes through the
n × n dual, an algebraically identical route. κ is exact, from the dual Gram's eigenvalues.
`THRESH = 1e-20` keeps the p·√thresh bound near 1e-6 with p ≈ 17–20k. A looser threshold would put
the bound above the control. A tighter one needs more passes.

**A tight threshold needs `maxit`.** At `thresh = 1e-20` with the default `maxit = 1e5`, glmnet
stopped the binomial path at λ 82 and the gaussian at λ 91 (Graviton4, a sizing run). It warned
"Convergence for 83th lambda value not reached … solutions for larger lambdas returned" and handed
back a shorter path. This run sets `control = list(maxit = 1e6)`, and both paths converge at all
100 λ. The check fails the run if a CV-selected λ falls below the certified path.

Reported, not asserted:
- A selects 77 genes at `lambda.1se`, with 0.88% CV misclassification.
- B selects 219 genes at `lambda.1se` with CV R² 0.911, GATA3 and AGR3 among them.
- At glmnet's default `thresh = 1e-7`, B's KKT residual is 3.957e-04.

## Across chips: what is a fixed point and what is a path

**Every decision is identical on all three chips:**
- the support at each of the 200 λ (5,802 and 8,848 nonzeros);
- the 77 and 219 selected genes;
- `lambda.1se`, the df sequence, and the binomial CV curve;
- the KKT residual, to every printed digit.

The λ sequences agree to 1.4e-16, and the gaussian CV curve to 2.1e-15 relative.

**The coefficient values differ only in their last bits.** Against Graviton4, both x86 chips differ
by at most 8.7e-15 (A) and 6.3e-13 (B) in absolute terms. Only 82 of 5,802 and 30 of 8,848
coefficients are bit-identical. The largest relative difference, 1.5e-9, is on coefficients that
have just entered the path. Each coefficient is a converged fixed point, so the difference is
floating-point rounding at that fixed point. It is reported in
[`results/compare.txt`](results/compare.txt), not asserted.

**Intel and AMD are byte-identical to each other**, every output file by sha256. They run the same
linux-64 build, and OpenBLAS 0.3.34 picks the same `Cooperlake` kernel on both: it has no Granite
Rapids or Zen 5 target. Graviton4 runs `neoversev2`. So the cross-chip difference here comes from
the arm64 and x86 builds, not from the vendor.

## Caveats

- One dataset and two designs. Multinomial over the 33 cancer types, `cox` on survival, and sparse
  input were not run.
- Genes with any missing value among a fit's samples are dropped: 4,196 in A, none in BRCA.
  Negative EB++ values (1.25 M cells) are floored at 0 before `log2(x + 1)`.
- The specs ran with `cost_limit` 1.5 and a 6 h TTL. They are retightened from these runs to 0.4 and
  3 h. Prices are on-demand us-west-2, as spawn printed them for these instances.

## Spend

Eleven launches, about **$0.70** in all. The three verifying runs cost $0.37 billed. The rest was the
fetch, two synthetic-data canaries and the sizing runs.
