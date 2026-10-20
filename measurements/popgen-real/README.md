# popgen on a real workload — 1000 Genomes Phase 3 and a 1000G-sized coalescent genome, three chips

> **scikit-allel reads all 77.8 M biallelic SNPs of 1000 Genomes Phase 3 and reproduces the
> consortium's own per-population allele counts on all 389 M rows. msprime simulates a whole
> 2.88 Gb genome for 3,336 haplotypes, and tskit and scikit-allel then agree on it to a few parts
> in 1e11.** Every count and statistic is identical on Graviton4, Intel and AMD. AMD is fastest
> and cheapest per result, Graviton4 is 4% behind it on cost, and Intel is 48% dearer.

Two workloads, one process per chromosome each, in one pool:

- **Real data:** the complete Phase 3 autosomes (22 VCFs, 15.5 GB gzipped, 2,504 samples in five
  super-populations). This is the reference panel itself, not a slice. Every biallelic ACGT SNP is
  counted per super-population, along with within- and between-population pairwise differences,
  Hudson's Fst for all 10 pairs, Watterson's S, Tajima's D and per-sample heterozygosity.
- **Simulation:** the same chromosome lengths (read from the VCFs' `##contig` lines, 2,881,033,286
  bp) for 661 + 503 + 504 diploids, the AFR/EUR/EAS sizes of 1000G. The model is Gutenkunst et al.
  2009's three-population Out-of-Africa model, with ρ = 1e-8 and the model's own μ = 2.35e-8.
  That gives 20.0 M marginal trees and 48.8 M mutations. The same statistics are then computed
  twice: by tskit from the trees, and by scikit-allel from the decoded genotypes. This is the
  scale at which msprime gets used for a cohort-sized null model.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/plink-real/stage-inputs.sh "$COOKBOOK_BUCKET"     # the VCFs, once; shared
bash measurements/popgen-real/stage-inputs.sh "$COOKBOOK_BUCKET"
for a in arm64 x86_64 amd; do
  spawn task run --spec "$(python3 scripts/spec_arch.py popgen $a "$COOKBOOK_BUCKET" "$(date +%s)" measurements/popgen-real/real.task.json)" --wait
done
R=measurements/popgen-real/results   # after copying each run's runs*/popgen-real/r1/ here
python3 measurements/popgen-real/crosschip.py $R/arm64/jobs.jsonl $R/x86_64/jobs.jsonl $R/amd/jobs.jsonl
```

`real.task.json` runs `run.sh` → [`real.py`](real.py), both pinned by sha256 in the command,
with 16 worker processes and one BLAS thread each. x86 uses the recipe's `popgen-39c447dd4e42`
twin, installed offline (the same msprime 1.4.4, tskit 1.0.3, scikit-allel 1.3.13 and numpy
2.5.4). `mkspec.py` writes the specs.

## Results

16 vCPU / 64 GiB, 16 processes, same input bytes, same versions:

| chip | instance | tool wall | billed | $/hr | compute $/result | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|
| Graviton4 | `m8g.4xlarge` | 1,618 s | 1,750 s | 0.7181 | 0.3227 | **0.3491** | 2.93 GiB |
| Xeon 6975P-C | `m8i.4xlarge` | 2,110 s | 2,199 s | 0.8467 | 0.4963 | 0.5172 | 2.84 GiB |
| **EPYC 9R45** | `m8a.4xlarge` | **1,166 s** | **1,240 s** | 0.9738 | **0.3154** | **0.3354** | 2.91 GiB |

A result is both workloads over the whole genome, with every check. *Tool wall* is `real.py`.
*Billed* is spawn's window, from launch to terminate, with $/hr taken from its `cost_estimate`.
*Peak RSS* is the largest single worker (`ru_maxrss`, a fresh process per job). The container
cgroup peaked at 18.0 / 20.8 / 20.8 GiB with 16 workers. The 15 GB of staged VCFs sit in the
host's tmpfs and come on top of that.

Per phase, summed over the 22 jobs of each kind:

| seconds | VCF parse + stats | msprime ancestry | msprime mutations | tree checks | tskit stats | allel on sim genotypes |
|---|---|---|---|---|---|---|
| Graviton4 | 15,647 | 5,362 | 123 | 53 | 161 | 1,035 |
| Intel | 25,099 | 4,985 | 174 | 65 | 202 | 1,027 |
| AMD | 11,504 | 4,234 | 103 | 39 | 99 | 447 |

- **The VCF parse is the bill.** scikit-allel's VCF reader does about 1 MB/s of gzipped VCF per
  process on Graviton4. AMD is 1.36× faster per process, and Intel 1.60× slower. On Intel, the 16
  processes share 8 physical cores (`threads_per_core` 2; 1 on Graviton4 and AMD), so this is a
  per-vCPU rate and not a per-core one. chr2 alone takes 1,005–2,110 s.
- **msprime's ancestry simulation is the second cost.** chr1 alone takes 538 s (AMD) to 712 s.
  Mutations, the tree checks and every tskit statistic together are under 7% of the sim jobs.
- **The wall is partly the queue order.** VCF jobs went first, largest first, then the sims. On
  Graviton4 the last VCF finished at 1,362 s and sim chr1 at 1,617 s. A single longest-first
  queue over both kinds would trim the tail. The per-phase sums above don't depend on the order.
- **AMD: 1.39× faster and 4% cheaper than Graviton4 billed; Intel: 30% slower and 48% dearer.**
  Overhead is 74–132 s (boot, Docker, image or env install, and 15.5 GB staged in-region).
- n = 1 per cell. Every count and statistic is deterministic and identical across chips (below),
  so a repeat would only re-measure timing. Sized from a two-chromosome probe on `m8g.4xlarge`
  (chr21+22: 211 s, 1.16 GiB per process) and a TTL of 3 h as a backstop, with `cost_limit` as
  the guard.

## What held at scale, on all three chips

Every row is asserted in-run; `results/<chip>/check.txt` has them all.

| claim | why it must hold | observed (identical on all three) |
|---|---|---|
| 24 inputs match sha256 | provenance | 24 of 24 |
| biallelic ACGT SNPs per chromosome == PLINK 1.9's import of the same bytes | two parsers, one filter ([plink-real](../plink-real/README.md)) | **22 of 22**, 77,818,345 |
| ALT count from GT == INFO `AC`; called alleles == `AN` == 5008 | the consortium's count | 0 mismatches |
| per super-population ALT count == round(`<POP>_AF` × 2N) | the consortium's published AF (4 decimals, 2N ≤ 1322, so exact) | **0 of 389,091,725** |
| five populations sum to `AC`; no missing calls | conservation; complete callset | 0 / 0 |
| **pairs partition**: C(n,2)·π_T == Σ C(n_k,2)·π_k + Σ n_k n_l d_kl, **every site** | the 12.5 M haplotype pairs split exactly into within and between | worst **2.9e-16** relative over 77.8 M sites |
| control: the same identity with the next site's between-terms | must fail, or the check is vacuous | fails at 94.6% of sites |
| `hudson_fst` num/den == between − mean within / between, every site | two allel code paths, one algebraic identity | **0** |
| every simulated tree: 1 root, 2n−2 = 6,670 edges; every internal node binary along the genome (edge-table sweep) | a Hudson coalescent | **0 of 19,992,103** trees |
| infinite sites: sites == mutations | the mutation model asked for | 48,802,039 == 48,802,039 |
| same seed reproduces chr22's tables; another seed does not | determinism, with its control | yes / no |
| **tskit (trees) == scikit-allel (genotypes)**: π per population, pooled π, divergence for 3 pairs | two implementations from different data; bound m·ε for m summed sites | worst **2.5e-11** (bound 1.8e-10 to 9.4e-10 by chromosome) |
| segregating sites, tskit == allel | integer | exact, 22 of 22 |
| Hudson Fst from tskit's π/d_xy == allel's `hudson_fst` | same estimator, different data | 4.6e-11 (bound 1.3e-7) |
| Tajima's D, tskit == formula on allel's π and S | — | 2.2e-11 |
| **site π == μ × branch π**, Poisson variance from the branch AFS: Σz² over 22 chromosomes < χ²₂₂(0.9999) = 55.5 | the mutation process given the genealogy (μ planted) | **Σz² 29.8**, max \|z\| 2.95 |
| mutations == μ × total branch length, same test | — | Σz² 16.5, max \|z\| 2.26 |

The m·ε bounds are the worst case for summing m positive terms in two different orders. The
Hudson bound is that times 1/Fst, because the subtraction amplifies it. The measured values sit
at least an order of magnitude inside them.

Observed, not asserted:

| super-population | pairwise diffs per haplotype pair | segregating SNPs | Tajima's D | het SNPs per sample | non-ref SNPs per sample |
|---|---|---|---|---|---|
| AFR (661) | 2,855,927 | 38,945,238 | −1.256 | 2,835,466 | 4,281,055 |
| AMR (347) | 2,260,825 | 25,914,509 | −1.138 | 2,195,721 | 3,602,977 |
| EAS (504) | 2,010,934 | 21,796,963 | −0.911 | 1,996,543 | 3,516,346 |
| EUR (503) | 2,150,322 | 22,132,889 | −0.804 | 2,138,108 | 3,518,567 |
| SAS (489) | 2,213,553 | 24,589,570 | −0.970 | 2,175,789 | 3,587,078 |

- **Hudson Fst** (± delete-one-chromosome jackknife SE): AFR–EAS 0.1500 ± 0.0013, AFR–EUR
  0.1233 ± 0.0012, AFR–SAS 0.1133, AFR–AMR 0.1052, AMR–EUR 0.0237, EUR–SAS 0.0333, EAS–EUR
  0.0995. The rest are in `check.txt`. The simulation gives YRI–CEU 0.144, YRI–CHB 0.161 and
  CEU–CHB 0.0997. Gutenkunst's model was fitted to other data, has no recent explosive growth,
  and compares a single population with 1000G's pooled super-populations, so the two sets are
  not expected to match.
- The paper's "a typical genome differs from the reference at 4.1–5.0 million sites" counts every
  variant type, including chrX. These are autosomal SNPs only, at 3.5–4.3 M per sample, with AFR
  highest.
- 67.6% of SNPs with any ALT carrier have it in only one super-population. That is not the
  paper's "86% of variants restricted to a single continental group", whose definition this run
  does not reproduce.
- 34,309,095 singletons. 224,132 biallelic SNP records carry **no** ALT allele in any of the
  2,504 samples (INFO `AC=0`, and agreed by the count check above). The 4 verbatim repeated
  records ([plink2-real](../plink2-real/README.md)) are counted twice, as PLINK does.

## Across chips: what is a fixed point and what is a path

**Every count and statistic is identical across the three chips, to the last digit of its
`repr`** ([`results/crosschip.txt`](results/crosschip.txt)). That covers all 1,694
per-chromosome values of the real-data jobs, and every site-mode statistic, count and tree of
the simulation. The two exceptions are a table digest and two reported expectations:

- **Intel == AMD byte for byte**, simulated table digests included (one x86 build, two vendors).
- **Graviton4 vs x86: the genealogy is byte-identical, but the mutations are not.** Re-simulating
  chr22 on each build and hashing column by column ([`colhash.py`](colhash.py),
  `results/*/colhash.txt`) shows `nodes`, `edges`, `mutations.node` and `mutations.site` are
  identical, and so is the branch AFS. `sites.position` and `mutations.time` differ by one ulp,
  for example `383.0451739756154` vs `383.04517397561534`. Every mutation lands on the same
  branch, in the same order, so every site statistic is identical. It is a path in the last bit
  of a float, consistent with a fused multiply-add in the arm64 build, but that was not isolated.
  It follows the build (arm64 env vs linux-64 twin), not the vendor.
- `pi_expected` and `pi_variance` (μ·B·w) differ at ≤ 1e-15 relative across builds, while
  `S_expected` (μ·ΣB) does not. They are `np.dot`, an OpenBLAS `ddot`: `neoversev2` on Graviton4
  and `Cooperlake` on both x86 chips (OpenBLAS 0.3.34 has no Zen 5 kernel, so AMD runs an
  Intel-named one). That is summation order in a reported expectation, and
  the z-scores agree to every printed digit.

## Pins

| | |
|---|---|
| data | RODA `s3://1000genomes/release/20130502/ALL.chr{1..22}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz` and `integrated_call_samples_v3.20130502.ALL.panel`, staged once by [plink-real](../plink-real/stage-inputs.sh) under `inputs/plink-real/` and read in place. sha256 are from `plink-real/vcf.sha256`, re-checked on every box. 1000 Genomes Project Consortium 2015, doi:10.1038/nature15393 |
| cross-code | PLINK 1.9's per-chromosome SNP counts, `plink-real/results/arm64/id-all.txt`, staged as `plink-real-id-all.txt` and pinned |
| model | Gutenkunst et al. 2009, PLoS Genet 5:e1000695, msprime-docs parameterisation, in `real.py`; seeds 20130502 + chromosome |
| scripts | `run.sh`, `real.py` (and `colhash.py`), staged by `stage-inputs.sh` and pinned by sha256 in each spec |
| image | the recipe's `quay.io/aarchsci/popgen@sha256:aac6868e…` (arm64) and its `popgen-39c447dd4e42` linux-64 twin |

## Caveats

π and Tajima's D here are over called SNPs with no accessibility mask, so they are per haplotype
pair, not per base pair. That is fine for comparing populations and chips, but not for comparing
with a masked published π. Indels, multiallelic records and chrX are excluded, as in plink-real.
