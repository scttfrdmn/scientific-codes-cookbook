# PLINK 1.9 on a real cohort: all of 1000 Genomes Phase 3, Graviton4, Intel and AMD

> **77,818,345 SNPs, 2,504 people: every allele count PLINK makes equals 1000 Genomes' own
> published count, overall and in all five super-populations, and the three chips agree byte for
> byte on everything downstream.** Graviton4 is 15% cheaper per run than Intel and 18% cheaper than
> AMD. Three-quarters of the run is a disk-bound phase that no chip speeds up.

Workload: the **complete** Phase 3 autosomes. That is all 22 genotype VCFs (15.5 GB gzipped,
about 0.8 TB of text) for the 2,504 unrelated samples of the final release. Every biallelic ACGT SNP
is imported, so this is not a subset. The pipeline is the QC-to-association path a GWAS takes:
1. import to `.bed`;
2. allele frequencies (overall and per super-population) and missingness;
3. a MAF ≥ 1% filter;
4. HWE, pooled and within EUR;
5. LD pruning (MAF ≥ 5%, `--indep-pairwise 50 5 0.2`);
6. a 10-PC PCA;
7. `--linear` on 12,057,350 SNPs, with and without the PCs.

The phenotype is planted, because 1000G has none. It is a 1.0-SD effect of one common SNP,
`22:49552130:A:G`, plus a 1.5-SD offset for AFR ancestry (the confound), plus N(0,1) noise. The
noise is a pinned file, so the phenotype is the same bytes on both chips.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/plink-real/stage-inputs.sh "$COOKBOOK_BUCKET"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py plink $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/plink-real/genome.task.json)" --wait
done
bash measurements/plink-real/compare.sh "$COOKBOOK_BUCKET"     # cross-chip bytes, results/
```

One task per chip runs `real.sh`, which is pinned by sha256, with one chromosome per core. The x86
legs (Intel and AMD) use `quay.io/biocontainers/plink`, the recipe's `images_x86_64` pin: the same 1.90b7.7 package
(the binary reports v1.9.0-b.8), bioconda's linux-64 build. The spec stages `inputs/plink-real/` as one
directory and runs `genome-task.sh`, checked against the sha256 in its command, because spawn's user data
is capped at 16 KiB. That body is the command the first two chips ran inline (`mkspec.py`), reading from
`/tmp/in`, plus a topology print.

## Results

16 vCPU, one `4xlarge` per chip, same input bytes, one chromosome per core:

| chip | instance | tool wall: chrom / PCA / assoc | tool wall | billed | $/hr | compute $/result | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 1746 / 15 / 515 s | 2364 s | 2568 s | 0.7181 | 0.4716 | **0.5122** | 4.98 GiB |
| Xeon 6975P-C | `m8i.4xlarge` | 1871 / 17 / 429 s | 2442 s | 2566 s | 0.8467 | 0.5743 | **0.6035** | 4.86 GiB |
| EPYC 9R45 | `m8a.4xlarge` | 1838 / 12 / 272 s | 2228 s | 2321 s | 0.9738 | 0.6027 | **0.6278** | 4.96 GiB |

*Chrom* is the per-chromosome phase: import, frequencies, QC, pruning and the identity checks.
*Tool wall* is all of `real.sh`, and *billed* runs from launch to terminate.

- **The per-chromosome phase is disk-bound on all three chips.** Only 4.9 (Graviton4), 6.2 (Intel)
  and 4.3 (AMD) of 16 cores were busy, and 52%, 42% and 53% of host CPU time was iowait (each
  chip's `samples.txt` over its phase window). The phase writes about 75 GB of
  `.bed` and text onto the container's root EBS volume. A single chromosome alone runs at
  0.5% iowait: chr8 took 208 s alone and 1,571 s with 15 others. So a bigger or faster chip
  buys nothing here; faster storage, or fewer chromosomes in flight, is the dial. AMD's phase
  (1,838 s) lands between the other two.
- **The association scan is compute-bound. AMD is 1.89× faster on it than Graviton4 and Intel
  1.20×** (515 / 429 / 272 s; about 12 busy cores and 0.1% iowait on Graviton4 and Intel). These are
  two builds of one version (aarch.bio vs bioconda; Intel and AMD share the bioconda one). `m8i`'s
  16 vCPU are 8 cores plus SMT, and `m8a`'s are 16 whole cores (`/proc/cpuinfo`: `siblings` ==
  `cpu cores` == 16). So the scan rate is an observation about these images and boxes, not a
  per-core rate.
- **Graviton4 is still cheaper per result**, because the disk-bound phase sets the wall time and
  Graviton4's hour is the cheapest. Billed: $0.51 against Intel's $0.60 (15% cheaper) and AMD's $0.63
  (18%). On tool wall alone: 18% and 22% cheaper. AMD's faster scan saves 243 s, and its 36%
  dearer hour more than spends that.
- **Overhead is small**: 204 s (Graviton4), 124 s (Intel) and 93 s (AMD) of the 2,300–2,570 s billed.
  That covers 44–48 s
  to stage 15.5 GB into tmpfs, ~25 s of Docker install, 3 s of image pull, plus boot and
  shutdown. Staging picks the family. 15.5 GB of VCFs need a 64 GiB box
  (tmpfs is half of RAM), while PLINK itself peaked at 5 GiB.
- n = 1 per cell. Every output is deterministic and byte-identical across chips (below). An earlier
  full pass on Graviton4 and Intel, same workload, ran within 1% of these tool walls. AMD has one
  run. The AMD leg cost $0.63.

### One PLINK per core needs one BLAS thread per PLINK

PLINK's `--threads 1` does not reach OpenBLAS, which starts a thread per core in **every
process**. With 16 chromosome jobs that is 256 threads on 16 cores. The `--linear` scan then ran at
**~80 SNPs/s per process**, against ~4,100/s with `OPENBLAS_NUM_THREADS=1`: a ~50× slowdown that
shows up as normal ~94% CPU per process. The whole-genome scan would have needed hours instead of
8.6 minutes. Set the BLAS thread count to 1 whenever you parallelise PLINK by chromosome. Note also
that GNU `nproc` honours `OMP_NUM_THREADS`, so read the core count before you export it.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| sha256 of all 22 VCFs and the panel == pins | provenance | 25 of 25 | 25 of 25 | 25 of 25 |
| PLINK keeps exactly the records awk reads as biallelic ACGT SNPs, in order | same records | 77,818,345 = 77,818,345 | same | same |
| ALT count `C1` == INFO `AC`, every SNP | 1000G's published count | 0 mismatches | 0 | 0 |
| `C1 + C2` == INFO `AN` (5008) | completeness | 0 mismatches | 0 | 0 |
| per super-population ALT count == round(`<POP>_AF` × N), all 389,091,725 rows | 1000G's published AF (4 decimals, N ≤ 1322, so exact) | 0 mismatches | 0 | 0 |
| the five populations' counts sum to `AC` | conservation | 0 mismatches | 0 | 0 |
| missing calls, every SNP and sample | the integrated callset is complete | 0 | 0 | 0 |
| `--maf 0.01` keeps exactly the SNPs with min(AC, AN−AC)/AN ≥ 0.01 | independent recount | 12,057,350, 0 disagree | same | same |
| 10 eigenvectors: max \|cos\| over 45 pairs < 1e-5 (6 printed s.f.) | orthogonality | 6.9e-08 | 6.9e-08 | 6.9e-08 |
| eigenvalues descending | definition | yes | yes | yes |
| every QC SNP has a `--linear` row | nothing dropped | 12,057,350 | same | same |
| planted SNP unbeaten once PCs are covariates | constructed truth | T² 583.2 vs next 70.1 | same | same |

Observed, not asserted:
- **HWE at 1e-6 fails 2,659,909 SNPs (22%) pooled, but 69,471 (0.6%) within EUR.** That is
  Wahlund's heterozygote deficit in a multi-ancestry sample: on a cohort like this, filter HWE
  within ancestry, never pooled.
- **The confound is real.** λGC is **75.69** unadjusted and **1.0003** with 10 PCs. Unadjusted, the top hit is
  `1:159174683:T:C`, which is rs2814778, the Duffy-null allele of *ACKR1*, near-fixed in Africa. The
  planted SNP ranks 62,679th. With PCs it ranks first.
- PC1 separates AFR (centroid +0.033) from everyone else, and PC2 separates EAS (−0.034) from EUR
  (+0.026). There are 465,311 pruned SNPs.
- 8 SNPs, all within 24 kb at chr2:91.80 Mb, come back `NA` from the PC-adjusted model. That is
  PLINK refusing a regression its collinearity check rejects. Which PC they collide with was not
  checked; long-range-LD regions were not excluded before the PCA.
- 4 records appear twice verbatim in the release, for example `8:89329148:G:A`. PLINK keeps both.

## Across chips: what is a fixed point and what is a path

Everything is a fixed point here, and it all agrees **byte for byte** on Graviton4, Intel and AMD:
- the eigenvectors and eigenvalues (OpenBLAS kernels differ by chip; at 6 printed figures the
  decomposition does not move);
- the top 1,000 adjusted and unadjusted hits;
- the phenotype;
- every per-chromosome tally;
- the NA list and the repeated-record list.

The counts are integers, so there was nothing to differ. The PCA and regressions are converged
linear algebra, printed at PLINK's precision. Unlike [fsl-real](../fsl-real/README.md), no path
appears in this workload. The fixture's cross-chip agreement survives at the scale it is used.
Intel and AMD run the same bioconda image, and on the AMD box its OpenBLAS (0.3.29) selects the
`Cooperlake` kernel. That was recorded in [plink2-real](../plink2-real/README.md)'s PLINK 1.9 task on
the same image; this task's own probe looked under `/opt/conda` and missed bioconda's `/usr/local`.
A different kernel moved no printed digit here. In plink2-real, PLINK 2's randomized PCA does move
between the same two x86 chips.

## Pins

| | |
|---|---|
| data | `s3://1000genomes/release/20130502/` (RODA): the 22 `ALL.chr*.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz` and `integrated_call_samples_v3.20130502.ALL.panel`, sha256 in `vcf.sha256` and `stage-inputs.sh`; chr22's hash also checked against a direct RODA download |
| noise | `noise.txt` sha256 `0414aadd…` (Python `random.Random(20130502).gauss`, generated by `stage-inputs.sh`) |
| script | `real.sh`, sha256 checked in-task against the staged `pins.sha256` |
| image | the recipe's `quay.io/aarchbio/plink@sha256:9d0a839e…` (arm64) and `quay.io/biocontainers/plink@sha256:b9fa9861…` (x86) |

The same VCF bytes under `inputs/plink-real/` are read by plink2-real and bcftools-real.
