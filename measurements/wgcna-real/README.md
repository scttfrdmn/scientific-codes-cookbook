# WGCNA on the whole TCGA breast-tumour cohort — Graviton4, Intel and AMD

> **One signed network over 15,045 genes × 1,083 BRCA tumours meets every closed form on all three
> chips, and puts every gene in the same module on all three.** The three breast-tumour intrinsic
> clusters (proliferation, luminal/ER, T cell) come out as three modules, 10 of 10 markers each.
> AMD runs the job 3.3× faster than Graviton4 and Intel 1.7× faster; AMD is the cheapest per result
> and Graviton4 the dearest.

Workload: every BRCA primary tumour in the TCGA PanCanAtlas RNA-seq matrix (Hoadley et al. 2018,
*Cell*; EB++-adjusted RSEM). That is 1,083 samples, one per patient, none flagged `Do_not_use`, and
every gene with no missing value and median log2(RSEM + 1) ≥ 3: **15,045 genes**. The network is the
WGCNA tutorial's (signed, `minModuleSize = 30`, `mergeCutHeight = 0.25`, `deepSplit = 2`) at power
12, the WGCNA FAQ's value for a signed network on ≥ 40 samples, built in **one block**, so the TOM
is exact over all 15,045 genes.

This is the size WGCNA is run at on bulk tumour RNA-seq: a whole cancer cohort and the whole
expressed transcriptome, not the top-5,000-by-variance shortcut. A 15k-gene TOM is 1.8 GB, and the
job peaks at 14.3 GiB, which is what decides the box.

**Data tier: a durable public id.** The matrix and its annotation table are GDC PanCanAtlas
publication files, fetched by GDC file UUID and md5-checked on AWS by
[`glmnet-real`](../glmnet-real/README.md). This measurement reads the same staged objects, and
[`pins.sha256`](pins.sha256) checks them again at every stage-in.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/wgcna-real/stage-inputs.sh "$B"     # stages glmnet-real's data first if absent
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py wgcna $a "$B" "$(date +%s)" \
    measurements/wgcna-real/real.task.json)" --wait
done
bash measurements/wgcna-real/collect.sh "$B" <scratch dir>   # results/, results/compare.txt
```

[`real.R`](real.R) is the workload and its checks. The analysis itself is three calls:

```r
sft <- pickSoftThreshold(X, powerVector = c(1:10, seq(12, 20, 2)), networkType = "signed")
net <- blockwiseModules(X, power = 12, networkType = "signed", TOMType = "signed",
                        minModuleSize = 30, mergeCutHeight = 0.25, deepSplit = 2,
                        maxBlockSize = ncol(X) + 1, numericLabels = TRUE)
```

## Results

`r8g.xlarge`, `r8i.xlarge` and `r8a.xlarge` (4 vCPU, 32 GiB), the recipe's aarch.bio image on
Graviton4 and bioconda's `linux/amd64` build on the two x86 chips, the same staged bytes:

| chip | instance | tool wall | billed | peak RSS | $/hr | compute $/result | billed $/result |
|---|---|---|---|---|---|---|---|
| Graviton4 | `r8g.xlarge` | 791 s | 883 s | 14.26 GiB | 0.2356 | 0.0518 | 0.0578 |
| Xeon 6975P-C | `r8i.xlarge` | 461 s | 546 s | 14.30 GiB | 0.2778 | 0.0356 | 0.0421 |
| **EPYC 9R45** | `r8a.xlarge` | **238 s** | **335 s** | 14.30 GiB | 0.3195 | **0.0211** | **0.0297** |

A result is the whole verified run: the analysis plus every check below. Tool wall is `Rscript`'s;
billed is spawn's command window.

| phase (s) | Graviton4 | Xeon | EPYC |
|---|---|---|---|
| read 1.88 GB, select the cohort | 17.0 | 9.7 | 8.2 |
| `pickSoftThreshold`, 15 powers | 36.6 | 19.0 | 14.0 |
| **`blockwiseModules`, one block** | **90.1** | **66.7** | **33.0** |
| `stats::cor` on 200 rows (a check) | 174.5 | 9.0 | 4.3 |
| TOM by itself, then its checks | 106.2 | 69.3 | 33.0 |
| the same network at `maxBlockSize = 5000` | 172.5 | 139.9 | 67.0 |
| permuted control | 65.2 | 44.8 | 23.4 |

- **The analysis a researcher runs takes 0.9–2.4 minutes**: read, the soft-threshold curve, one
  `blockwiseModules`. AMD does it 2.6× faster than Graviton4 and Intel 1.5× faster. The rest of each
  run is verification, so the totals price the checks as well as the science.
- **AMD is the cheapest per result by a wide margin**: 3.3× Graviton4's speed for a 36% dearer hour,
  so 49% cheaper billed. Intel is 1.7× faster for an 18% dearer hour, 27% cheaper billed. Intel's
  4 vCPU are 2 cores × 2 threads; AMD's and Graviton4's are 4 full cores.
- **The TOM and the clustering are where the chips separate** (TOM 45.7 / 26.4 / 12.1 s). Both are
  dense matrix work through each build's OpenBLAS: `neoversev2` in 0.3.34 on Graviton4, `Cooperlake`
  in 0.3.30 on both x86 chips.
- **`stats::cor` is 19–40× slower on Graviton4 than on x86, and the reason is the platform, not the
  chip.** R's `cov.c` accumulates every inner product in `LDOUBLE`. `long double` is 128-bit IEEE
  quad on aarch64 Linux, done in software, and 80-bit x87 in hardware on x86. A full 15k × 15k
  `stats::cor` took **6,579 s** on Graviton4 in a sizing run, against 4 s for `WGCNA::cor`, which is
  why the check runs on 200 seeded rows. Use `WGCNA::cor` (or `crossprod` of the scaled matrix) on
  arm64; never `stats::cor` on a whole expression matrix.
- **The box is sized by memory**: 14.3 GiB peak plus 1.9 GB staged in tmpfs, so a 16 GiB `m8g.xlarge`
  would not fit. Average busy cores were 2.7–3.0 of 4.
- n = 1 per chip. WGCNA is deterministic here: two `blockwiseModules` runs gave identical colours and
  eigengenes, and the TOM was identical at 1 and 4 threads, on every chip.

## What held at scale, on all three chips

Every row is asserted inside the task by [`real.R`](real.R); a failure fails the run.

| claim | why it must hold | Graviton4 | Xeon | EPYC |
|---|---|---|---|---|
| 20,531 × 11,069 release, unique ids; 1,083 BRCA primaries, one per patient | the release | yes | yes | yes |
| `goodSamplesGenes` | WGCNA's own QC | allOK | allOK | allOK |
| `WGCNA::cor` vs `crossprod(scale(X))/(n−1)`, all 15,045² entries, ≤ 1e-12 | closed form | 1.4e-15 | 2.7e-15 | 2.7e-15 |
| `WGCNA::cor` vs `stats::cor`, 200 seeded rows × 15,045, ≤ 1e-12 | second implementation | 5.6e-16 | 8.9e-16 | 8.9e-16 |
| signed adjacency vs `((1+cor)/2)^12`, all entries, ≤ 1e-12 | closed form | 1.1e-14 | 1.6e-14 | 1.6e-14 |
| TOM on a 4-node matrix worked by hand, ≤ 1e-15 | dyadic, exact | 0 | 0 | 0 |
| **TOM vs `(L + A)/(min(kᵢ,kⱼ) + 1 − A)`, 500 seeded rows × 15,045, ≤ 1e-12** | **closed form** | **2.9e-15** | **2.9e-15** | **2.9e-15** |
| TOM symmetric; TOM at 1 vs 4 threads | exact | identical | identical | identical |
| `blockwiseModules`, run twice | determinism | identical | identical | identical |
| every eigengene vs PC1 of `scale(module)`, 25 modules, ≤ 1e-12 | closed form | 4.3e-14 | 4.3e-14 | 4.3e-14 |
| `kWithin` vs `rowSums(A[module, module]) − 1`, relative, ≤ 1e-12 | definition | 2.2e-16 | 2.2e-16 | 2.2e-16 |
| **proliferation markers (MKI67, TOP2A, CCNB1 …) share one module** | **published clusters** | **10 of 10** | **10 of 10** | **10 of 10** |
| **luminal/ER markers (ESR1, GATA3, FOXA1 …) share one module** | **published clusters** | **10 of 10** | **10 of 10** | **10 of 10** |
| **T-cell markers (CD2, CD3E, LCK …) share one module** | **published clusters** | **10 of 10** | **10 of 10** | **10 of 10** |
| the three are distinct modules | published clusters | 11, 4, 6 | 11, 4, 6 | 11, 4, 6 |
| control, every gene permuted independently: \|ARI\| vs the real modules ≤ 0.05 | null | 0, no modules | 0, no modules | 0, no modules |

The tolerances are the recipe's: two computations of a quantity in [−1, 1] or [0, 1] that differ
only in summation order, with ~4,500 epsilons of headroom. The marker sets are the breast-tumour
intrinsic gene clusters of Perou et al. 2000 and Sørlie et al. 2001, ten well-known genes each. The
assertion is ≥ 80% of each set in one non-grey module. All three sets landed whole, which is the
strongest form of the claim: nothing tuned it. Each chip's values are in its `results/<chip>/score.tsv`.

Reported, not asserted:
- **25 modules** (1,698 genes down to 65); 1,984 genes grey.
- Hubs by `kWithin` include TPX2 (proliferation), MLPH (luminal) and SASH3 (T cell). ESR1 correlates
  with the luminal eigengene at 0.861.
- `pickSoftThreshold`'s `powerEstimate` is **14**. Signed R² is 0.849 at power 12, just short of the
  0.85 cut, and 0.885 at 14. Mean connectivity is 31.5 at 12 and 16.1 at 14.
- **The default `maxBlockSize = 5000` gives a different answer.** It splits the genes into 4 blocks,
  finds **39** modules instead of 25, and agrees with the one-block network at **ARI 0.50**. This is
  the approximation the recipe page warns about, measured on real data. If the TOM fits in RAM
  (n² × 8 bytes, several times over at peak), run one block.

## Across chips: what is a fixed point and what is a path

**Every discrete answer is identical on all three chips:** each of the 15,045 genes' module in the
one-block network, in the 4-block network and in the permuted control; the module sizes; the hubs;
`powerEstimate`; and the TOM's sum to all 16 printed digits (8.887362192149486e+05).

**The floating-point values differ only in their last bits.** Against Graviton4, both x86 chips'
eigengenes differ by at most 1.7e-15. 7,421 of 15,045 `kWithin` values are bit-identical, and the
rest differ by at most 3.0e-15 relative. Of the residuals in the table above, only the three `cor` and adjacency
ones move, by under 2×. These are converged quantities, so the difference is rounding, and it is
reported, not asserted. Clustering a TOM that agrees to ~1e-15 does not move a cut.

**Intel and AMD are byte-identical to each other**, every output file by sha256. They run the same
bioconda build, and OpenBLAS 0.3.30 picks `Cooperlake` on both. Graviton4 runs a different build
(aarch.bio's, R 4.5.3 and OpenBLAS 0.3.34 against bioconda's R 4.5.2 and 0.3.30), so the cross-chip
difference comes from the arm64 and x86 builds, not from the vendor.

## Caveats

- One cohort and one network type. Unsigned networks, consensus networks across cancers,
  module–trait regression and the blockwise path at other block sizes were not run.
- Negative EB++ values are floored at 0 before `log2(x + 1)`. No sample-outlier step was applied
  beyond `goodSamplesGenes`.
- Prices are on-demand us-west-2, as spawn printed them for these instances. The specs ran with
  `cost_limit` 0.4 and a 1h30m TTL. They are retightened from these runs to 0.2 and 1 h.

## Spend

Four launches, about **$0.61** in all. The three verifying runs cost $0.13 billed. The rest was one
Graviton4 sizing run (7,299 s), 90% of it a full-matrix `stats::cor`.
