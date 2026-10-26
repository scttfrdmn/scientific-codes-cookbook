# PLINK 2 on a real workload — 1000 Genomes Phase 3, Graviton4, Intel and AMD

> **The whole of 1000 Genomes Phase 3 (77.8 M SNPs × 2,504 samples) goes from VCF to a
> PC-adjusted GWAS in 11 minutes of PLINK 2 on Graviton4, 1.18× faster and 28% cheaper than Intel.
> AMD is the fastest (10 minutes) and costs 20% more than Graviton4 per result on PLINK's own time.**
> Every allele count equals the consortium's own and PLINK 1.9's on the same bytes, exactly; the
> exact-PCA eigenvalues of the two PLINKs are identical; eleven planted effects come back within their SEs.

Workload: chromosomes 1–22 of the 1000 Genomes Phase 3 release, all 2,504 samples, the same RODA
objects [recipes/plink](../../recipes/plink/README.md)'s real run reads. This is the reference
panel itself — the cohort people actually run ancestry PCA and QC on, not a slice of it. The
workflow is the standard one: VCF import → merge → allele counts, HWE, missingness → QC
(MAF ≥ 1%, call rate ≥ 98%) → LD pruning → `--pca 10 approx` → `--glm` with 10 PCs.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/plink2-real/stage-inputs.sh "$COOKBOOK_BUCKET"
for A in arm64 x86_64 amd; do for S in p2 p19; do
  spawn task run --spec "$(python3 scripts/spec_arch.py plink2 $A "$COOKBOOK_BUCKET" "$(date +%s)" measurements/plink2-real/$S.task.json)" --wait
done; done
```

`p2.sh` (PLINK 2's image) runs the workflow and its identities; `p19.sh` (PLINK 1.9's image) then
converts the same VCFs and compares. Both scripts are staged and pinned by sha256. Since the AMD
leg, each spec stages its inputs as directories and runs a staged task body (`p2-task.sh`,
`p19-task.sh`, sha256 in the spec's command), because spawn's user data is capped at 16 KiB. The
body links the inputs into `/tmp` and collects the outputs into one directory around the same
`p2.sh`/`p19.sh` call. It also stages itself as a file under `/tmp`. Without a manifest file
directly in `/tmp`, spawn bind-mounts only the staged directories, and `/tmp` is the container's
20 GiB root disk. The first AMD attempts died there, `No space left on device` writing `kg.pgen`.
[`cross-chip.py`](cross-chip.py) compares two chips' outputs in the bucket.

## Results

16 vCPU / 128 GiB, 16 threads, same PLINK 2 build (`v2.0.0-a.7.8`) on all three, same input bytes:

| chip | instance | PLINK 2 wall | task wall | billed | $/hr | **compute $/result** | billed $/result | peak mem |
|---|---|---|---|---|---|---|---|---|
| **Graviton4** | `r8g.4xlarge` | 681 s | 1,127 s | 1,206 s | 0.9426 | **0.178** | **0.316** | 33.2 GiB |
| Intel Xeon 6 | `r8i.4xlarge` | 804 s | 2,312 s | 2,389 s | 1.1114 | 0.248 | 0.738 | 33.1 GiB |
| AMD EPYC 9R45 | `r8a.4xlarge` | **601 s** | 1,762 s | 1,799 s | 1.2781 | 0.213 | 0.639 | 33.3 GiB |

A result is one genome-wide run of the workflow. **PLINK 2 wall** is the union of PLINK 2's own
Start/End times from its 75 logs ([`toolwall.py`](toolwall.py)); task wall adds the awk that
checks identities over 78 M rows. Billed is spawn's window, wrapper start to stage-out done ([`results/billed-windows.txt`](results/billed-windows.txt)).

- **Graviton4: 1.18× faster, 28% cheaper per result** on PLINK 2's own time. VCF import is the bulk
  (422 vs 531 s summed over 22 chromosomes, 1.26×); merge (114 / 112 s) and genome-wide
  counts (18 / 18 s) tie, and x86 wins exact PCA (15.7 vs 9.9 s) and the PC-adjusted `--glm` (17.6 vs 15.9 s).
- **AMD: 1.13× faster than Graviton4 on PLINK 2's own time, and 20% dearer per result**, because its
  hour costs 36% more. Import ties with Graviton4 (418 s summed). AMD wins everything after it: merge
  82 s, counts 12.6 s, exact PCA 9.5 s, PC-adjusted `--glm` 9.3 s. Against Intel, on the same image,
  AMD is 1.34× faster and 14% cheaper. `r8a`'s 16 vCPU are 16 whole cores; `r8i`'s are 8 cores with
  SMT. On the AMD box OpenBLAS picks `Cooperlake` in both images (0.3.34 in plink2's, 0.3.29 in
  plink's; neither has a Zen 5 kernel). The Intel leg's kernel was not recorded.
- **The billed gap is not PLINK.** Bioconda's x86 image has a BusyBox userland, and the non-PLINK
  part of the task (the identity checks: awk, sort, gzip) took 1,508 s there on Intel and 1,161 s
  on AMD, against 446 s in the arm64 image. A run without them would
  bill ≈ PLINK 2 wall + 37–79 s of Docker install, image pull and staging: about **$0.20 (Graviton4), $0.27 (Intel) and
  $0.23 (AMD)**.
- **Peak memory 33 GiB on all three** is the container cgroup, which includes the ~27 GiB of `/tmp` the task
  wrote (merged `.pgen`, counts); `/tmp` held 40.8 GiB at the end, with the 14.6 GB of staged VCFs.
  That, not PLINK, is why this is an `r8g`: a 64 GiB box has a 32 GiB tmpfs.
- PLINK 1.9's side (`p19.sh`, 8 chromosomes at a time), task wall / billed: 496 / 573 s, $0.150
  billed on Graviton4; 632 / 709 s, $0.219 on Intel; 494 / 534 s, $0.190 on AMD (billed windows
  from spawn's completion records, [`results/billed-windows.txt`](results/billed-windows.txt)). Per chromosome, 1.9's single-threaded VCF
  conversion was ~1.2× faster on Intel (chr2 209 vs 254 s) and 1.5× on AMD (167 s), but the 1.9
  builds differ (bioconda x86 `1.90b7.7`, aarchbio `1.9.0-b.8`), so that is an observation, not an
  attribution.
- n = 1 per cell: PLINK 2 here is deterministic on a chip (a second Graviton4 run gave
  byte-identical eigenvectors and regression rows), so a repeat would re-measure only timing.
- The AMD legs cost $0.83 billed (p2 + p19). The two failed p2 attempts cost $0.85 more.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| 23 inputs match their sha256 | provenance | ok | ok | ok |
| `ALT_CTS` == the VCF's INFO `AC`, `OBS_CT` == `AN`, every variant | 1000G's own counts, same genotypes | **77,818,345 of 77,818,345** | same | same |
| `--hardy` classes sum to 2,504 and imply `--freq`'s count | two code paths, one integer | 77,818,337 of 77,818,337 | same | same |
| `--maf 0.01` keeps exactly the variants `.acount` says it should | recount from integers | 12,057,348 == 12,057,348 | same | same |
| PLINK 1.9 keeps the same variants, in the same order | same filters, same bytes | 77,818,337 | same | same |
| **minor-allele and total counts == PLINK 1.9's** | integers, same bytes | **0 mismatches** | 0 | 0 |
| HWE P == PLINK 1.9's, relative | 1.9 prints 4 sig. figs (≤ 5e-4), PLINK 2 six | max **4.998e-4** | 4.998e-4 | 4.998e-4 |
| **exact-PCA eigenvalues == PLINK 1.9's** | same GRM, same eigenproblem; 6 sig. figs | **identical** | identical | identical |
| eigenvectors orthonormal; every PC sums to 0 | centred GRM; bounds from printed loadings | 1.3e-7 / 3.2e-6 | 1.4e-7 / 2.9e-6 | 9.6e-8 / 2.8e-6 |
| **11 planted betas within 4 SE**; Σz² < χ²₁₁(0.999) = 31.26 | the estimator is unbiased | **max \|z\| 1.66, Σz² 9.035** | same | same |
| 1.9 `--linear` == `--glm` on the planted SNPs (BETA, t) | same OLS; covariate scaling cannot move a SNP's coefficient | 3.0e-4 / 1.1e-4 | same | same |

The phenotype is simulated in `p2.sh`: one causal SNP per odd autosome (MAF ≥ 0.25, pruned-in),
betas ±0.10–0.30 per ALT allele, an AFR shift of +1.0, and N(0,1)-shaped noise from an integer LCG
— so its bytes are identical on all three chips (sha256 `b985fdb2…`). Genomic control on the
even-autosome nulls: **λ = 66.9 without PCs, 1.0053 with them** on all three chips — the planted
stratification, and the 10 PCs removing it. 7 variants reach P < 5e-8, 5 of them planted.

## Across chips: what is a fixed point and what is a path

- **Every count is a fixed point** and equal (each is asserted against the VCF on each chip).
- **Both eigendecompositions are fixed points.** Exact and approx eigenvalues are identical to
  the 6 printed figures across chips (and approx == exact). The approx eigenvectors agree to
  ≤ 2.0e-6 per loading — **after a sign**: PCs 6, 8, 9 and 10 come out negated on x86. That is
  the eigenproblem's own ambiguity, not a difference; never compare PCs across machines unsigned.
- **The regression is a fixed point to printed precision**: BETA differs by ≤ 8.8e-6 relative,
  t by ≤ 1.7e-6, SE not at all — the covariates differ in their sixth digit. PLINK 1.9's
  `lin.assoc.linear` is byte-identical across chips. λ, Σz² and the recovery z's agree to every
  printed digit.
- **AMD against Graviton4 and against Intel** ([`results/cross-chip-amd.txt`](results/cross-chip-amd.txt)).
  The counts (`p2.counts.tsv.gz` content), pruned set, planted SNPs, phenotype, both eigenvalue
  files, PLINK 1.9's eigenvalues and `lin.assoc.linear` are byte-identical on all three chips.
  The approx eigenvectors are not, and the split falls inside x86. AMD agrees with Graviton4 to
  ≤ 5.7e-6 per loading after a sign (PCs 6, 9, 10 negated). It agrees with Intel, **same image
  and digest**, to ≤ 4.1e-6 (PC 8 negated). The regression follows the covariates: AMD's BETA
  equals Intel's exactly and Graviton4's to ≤ 8.8e-6 relative; t differs by ≤ 1.3e-6 from Intel
  and ≤ 1.7e-6 from Graviton4. So the randomized PCA's last printed digits are a path through
  each box's BLAS (the AMD box runs OpenBLAS's `Cooperlake` kernel), even between two x86 chips.
  Every assertion passes on every chip. The orthonormality deviation moves with the eigenvectors
  (9.6e-8 on AMD) and stays far inside its bound.

## Four things that would have faked a result

- **`--glm`'s A1 is the minor allele, not ALT** — REF on 5 of the 11 planted SNPs. Comparing BETA
  to an ALT-coded truth without checking A1 reports five sign errors in a correct regression.
- **`--pca approx` is randomized.** Unseeded, two runs on one box gave PC1 opposite signs; with
  `--seed` it is byte-reproducible.
- **`--pmerge-list` silently collapsed 4 repeated `CHR:POS:REF:ALT` records** into the merged set
  (8 records, 4 IDs; [`results/arm64/dup.txt`](results/arm64/dup.txt)). They are removed from both
  PLINKs explicitly with `--rm-dup exclude-all`, so the comparison stays on identical variants.
- **PLINK 1.9 drops every phenotype of a sample with no sex**, and the VCFs carry none: without
  `--allow-no-sex`, `--linear` skips with a warning and writes nothing.

## Pins

| | |
|---|---|
| data | RODA `s3://1000genomes/release/20130502/ALL.chr{1..22}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz` (14.6 GB, written 2015) and `integrated_call_samples_v3.20130502.ALL.panel`; sha256 for all 23 in [`stage-inputs.sh`](stage-inputs.sh), checked on the box. 1000 Genomes Project Consortium 2015, doi:10.1038/nature15393 |
| images | the recipe's PLINK 2 and PLINK 1.9 pins, arm64 and x86 (frontmatter of [recipes/plink2](../../recipes/plink2/README.md)) |
| scripts | `p2.sh`, `p19.sh`, staged and pinned by sha256 in `p2.task.json` / `p19.task.json` |

Per-chip scores, regression rows and eigenvalues are in [`results/`](results/).

## Caveats

n = 1 per chip at the final spec (AMD: the third attempt, after two that ran out of `/tmp`), plus an earlier Graviton4 run differing only in a `sha256sum`
flag, whose science outputs were byte-identical. Sized from a two-chromosome probe on the same
box (32 s of PLINK 2 for 2.1 M variants). The planted SNPs are the first eligible ones on each
chromosome, so several sit in subtelomeric sequence; that changes nothing about the estimator.
