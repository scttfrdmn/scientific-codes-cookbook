# Beagle and Minimac4 on a real chromosome: 1000 Genomes chr20, a held-out cohort, three chips

> **250 people, typed only at the 55,178 chr20 sites of a real Illumina Omni2.5, imputed by both
> tools to all 1,653,582 panel SNPs, then scored against their own sequenced genotypes:
> 399,601,000 masked genotypes per tool.** Both reach dosage r² 0.983 on common variants and agree
> on 99.93% of hard calls. Every output is byte-identical on Graviton4, Intel and AMD. Graviton4 is
> cheapest billed, AMD cheapest in compute, within 6% of each other; Intel is last on both.

Workload: the **complete** chr20 of 1000 Genomes Phase 3, split into a panel and a held-out cohort.
1. **Cohort:** every 10th of the 2,504 samples in file order, 250 people. That covers all 26
   populations (65 AFR, 33 AMR, 52 EAS, 50 EUR, 50 SAS).
2. **Panel:** the other 2,254 samples at every biallelic SNP that is polymorphic among them. That
   is 1,653,582 sites; one position carrying two records is dropped.
3. **Array:** the panel sites that are on the Omni2.5, matched on CHROM/POS/REF/ALT. The site list
   comes from the consortium's own chip genotypes of its samples. 55,178 of the chip's 59,253 chr20
   sites match, and none match only with swapped alleles.
4. **Target:** the cohort's genotypes at the array sites with the phase removed, which is what a
   genotyped cohort hands an imputation server.
5. **Truth:** the cohort's sequenced genotypes at every panel site. Neither tool sees them.

This is the leave-out design imputation benchmarks use, on the 1000G Phase 3 panel that public
imputation servers offer, a whole chromosome at a real array's density. A real cohort is larger than 250.
Both tools' cost grows with the target count, so read the per-cohort cost as one 250-person batch.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/imputation-real/stage-inputs.sh "$COOKBOOK_BUCKET"   # once: chip sites, scripts, pins
for a in arm64 x86_64 amd; do
  for s in $(python3 scripts/spec_arch.py imputation $a "$COOKBOOK_BUCKET" "$(date +%s)" \
      measurements/imputation-real/0{1-prep,2-beagle,3-mm4}.task.json); do
    spawn task run --spec "$s" --wait
  done
done
spawn task run --spec "$(python3 scripts/spec_arch.py imputation arm64 "$COOKBOOK_BUCKET" "$(date +%s)" \
  measurements/imputation-real/04-diff.task.json)" --wait          # Minimac4's VCFs, field by field
bash measurements/imputation-real/collect.sh "$COOKBOOK_BUCKET"   # results/, compare.txt
```

There are four tasks per chip, each reading its inputs from S3 and checking every staged script
and input against `pins.sha256`:
- `01-prep`, in the Minimac4 image (which ships bcftools): the split, the mask and `minimac4
  --compress-reference`.
- `02-beagle`: Beagle phases and imputes the cohort.
- `03-mm4`: Minimac4 imputes the same cohort from Beagle's phase at the array sites. It needs a
  phased target, and phase-then-impute is the shape of an imputation server. `score.py` then
  scores both tools.

The specs come from `mkspec.py`. The x86 legs run bioconda's builds of the same versions, which
are the recipe's `images_x86_64`.

## Results

16 vCPU, one `4xlarge` per task, 16 threads for both tools, same input bytes:

| chip | instance | Beagle | Minimac4 compress / impute | prep + score | tool wall | billed | $/hr | compute $/result | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` ×3 | 64 s | 108 / 54 s | 374 + 38 s | 638 s | 1,101 s | 0.6381 | 0.1131 | **0.1952** | 8.29 GiB (Beagle JVM) |
| Xeon 6975P-C | `c8i.4xlarge` ×3 | 89 s | 99 / 53 s | 338 + 50 s | 629 s | 1,157 s | 0.7497 | 0.1310 | **0.2409** | 6.08 GiB |
| EPYC 9R45 | `c8a.4xlarge` ×3 | **52 s** | 71 / **37 s** | 251 + 31 s | **442 s** | **845 s** | 0.8622 | **0.1059** | **0.2024** | 8.19 GiB |

*Tool wall* is every timed step of the chain. *Prep* is the bcftools split and mask, without the
compress step. *Billed* runs from launch to terminate on all three instances (`results/billing.tsv`).
$/hr is the on-demand price spawn uses for these types in us-west-2. A result is the whole verified
chain.

- **The imputation itself is about two minutes.** Beagle plus Minimac4's impute takes 118 s on
  Graviton4, 142 s on Intel and 89 s on AMD. At $0.021, $0.030 and $0.021 that is a tie between
  Graviton4 and AMD for every further cohort against a panel that is already built.
- **The one-time panel build is most of the timed wall** (69–76%). bcftools subsets 2,254 samples
  out of the release in 191–283 s, single-threaded. A real study builds the panel once.
- **AMD is fastest on every step.** It is 1.23× Graviton4 on Beagle, 1.46× on Minimac4's impute
  and 1.44× on the whole chain. Its hour is 35% dearer, so it is 6% cheaper in compute and 4%
  dearer billed: boot and image pulls are a fixed cost that a faster chip does not shrink.
- **Intel is slowest on Beagle** (89 s, Graviton4 is 1.39× faster). Its 16 vCPU are 8 cores with
  SMT (2 threads per core on every Intel box; 1 on Graviton4 and AMD), so this is a per-instance
  comparison, not a per-core rate. It is the dearest on both measures.
- **Minimac4's impute is output-bound.** Its own log gives 40 of 54 s on Graviton4 (26 of 37 on AMD)
  to writing the 1.65M-record GT:DS VCF; its HMM takes 8–12 s.
- **Memory is not the constraint.** Beagle ran with `-Xmx24g`. The JVM's peak was 6.1–8.3 GiB,
  which reflects the collector's choices under that ceiling, not a requirement. Minimac4 peaked at
  2.18 GiB on every chip. Staging peaked at 0.7 GiB of tmpfs, with ≤3.5 GiB of work files on disk.
- **Overhead is 42–48% of each bill:** three boxes, each booting and pulling an image. These timings
  are not compute cost.
- n = 1 per chip. Both tools are deterministic at a fixed seed and thread count, and every output
  is byte-identical across chips (below), so a repeat would only re-measure timing noise.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| sha256 of the chr20 VCF, the chip site list and every script == pins | provenance | ok | ok | ok |
| cohort and panel share no sample | a leave-out | 0 | 0 | 0 |
| AN == 4,508 and 0 < AC < AN at every panel site | panel completeness, and polymorphism after the subset | 0 violations | 0 | 0 |
| truth sites == panel sites, in order (md5) | same records | equal | equal | equal |
| both tools' records == the panel's, in lockstep, key by key | nothing dropped, added or reordered | 1,653,582, 0 out of step | same | same |
| holes (missing GT or DS) in either tool's output | an imputer must fill what it is asked for | 0 | 0 | 0 |
| dosages outside [0, 2] | definition | 0 | 0 | 0 |
| masked genotypes scored == masked sites × 250, per tool | every one counted | 399,601,000 | same | same |
| Minimac4 `TYPED` records == array sites | it saw exactly the array | 55,178 | same | same |
| Beagle changes no typed genotype | it treats observed calls as fixed | 0 of 13,794,500 | 0 | 0 |
| \|INFO AF − mean(DS)/2\| within print rounding (Beagle ≤ 0.00255, Minimac4 ≤ 0.00025) | each tool's AF is the mean of its own dosages | 0.000320 / 0.000002 | same | same |
| dosage r² vs truth, panel MAF ≥ 5%, ≥ 0.90, per tool | the truth | 0.9827 / 0.9827 | same | same |
| Beagle vs Minimac4 hard-call agreement on masked genotypes ≥ 0.99 | cross-validation | 0.99925 | same | same |

The two floors are bands, set well below the measured 0.983 and 0.99925, so they catch a broken
run (a wrong panel, a shifted mask), not noise. The rest are exact, with no tolerance. A truncated or partly written output fails the lockstep, hole and count checks, even
where its concordance would pass.

Accuracy by panel MAF, masked genotypes, Beagle / Minimac4 (`results/*/mm4/scores.tsv`):

| panel MAF | sites | dosage r² | non-ref concordance | hard-call agreement between tools |
|---|---|---|---|---|
| < 0.1% | 1,055,831 | 0.668 / 0.666 | 0.471 / 0.467 | 0.99985 |
| 0.1–0.5% | 251,034 | 0.886 / 0.887 | 0.769 / 0.773 | 0.99938 |
| 0.5–1% | 72,883 | 0.945 / 0.945 | 0.874 / 0.875 | 0.99882 |
| 1–5% | 103,540 | 0.969 / 0.969 | 0.934 / 0.934 | 0.99807 |
| ≥ 5% | 115,116 | 0.983 / 0.983 | 0.974 / 0.975 | 0.99480 |

Observed, not asserted:
- **The two tools are equally accurate in every bin**, within 0.003 r². They are two codebases with
  two HMMs.
- **They fail on the same genotypes.** 564,093 masked genotypes are wrong in both, which is 79% of
  each tool's errors (713,877 Beagle, 710,215 Minimac4). The residual is set by the panel and the
  array, not by either implementation, the same reading as the recipe's fixture.
- **Minimac4 rewrites the calls you gave it, at scale.** It changed 26,353 of 13,794,500 typed
  genotypes (0.19%) to its own estimate, where Beagle changed none. That is the recipe's fixture
  finding, still present on a real panel.
- 66% of masked sites are below 0.1% panel MAF. That bin's hard-call concordance (0.9995) is almost all
  homozygous-reference calls, which is why the table leads with dosage r² and non-reference
  concordance.
- On common variants each tool's own accuracy estimate tracks the truth: mean per-site DR2 0.984
  and R2 0.980, against 0.983 measured. In rare bins the per-site mean and the pooled r² are
  different statistics and are not compared.

## Across chips: what is a fixed point and what is a path

Every output is **byte-identical** on all three chips (`results/compare.txt`, `results/diff/`):
- the panel, the truth and the target;
- Beagle's VCF, from two JDK builds (25.0.2 on aarch.bio, 24.0.2 on bioconda);
- Minimac4's VCF, from two builds, all 1,653,582 records compared field by field;
- the whole score table.

Beagle's phasing is an iterative, seeded search. At a fixed seed and 16 threads it reached the same
haplotypes on every chip, so here it behaves as a fixed point. The thread count was not varied.

**Only the `.msav` panel differs**, with three different md5s, Intel and AMD included, though they
ran the same binary. Minimac4 imputes byte-identical output from all three. So the difference is
in the file, not in the panel it encodes; its cause was not measured.

## Caveats

- **This is an upper bound for a real array cohort.** Cohort and panel come from one callset, so
  there is no genotyping error, strand or batch difference between them, and the cohort's
  populations are all in the panel.
- **Defaults throughout:** no genetic map, so both tools use 1 cM/Mb; Beagle's seed; one chromosome.
  A real run passes the map (`map=` for Beagle, `-m` for Minimac4).
- The cohort is every 10th sample, not a random draw. It spans all 26 populations.

## Pins

| | |
|---|---|
| cohort + panel | `s3://1000genomes/release/20130502/ALL.chr20.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz` (RODA), sha256 `4a0b61d6…`, the bytes plink-real staged under `inputs/plink-real/`, reused |
| array | `s3://1000genomes/release/20130502/supporting/hd_genotype_chip/ALL.chip.omni_broad_sanger_combined.20140818.snps.genotypes.vcf.gz` (RODA), sha256 `24060fab…` measured on AWS by `00-sites`; its chr20 site list `omni.chr20.sites.tsv` sha256 `a44a01cc…` is what the tasks pin |
| scripts | `lib.sh`, `prep.sh`, `beagle.sh`, `mm4.sh`, `score.py`, `vcfdiff.py`, `sites.sh`, all in `pins.sha256` |
| images | the recipe's `quay.io/aarchbio/minimac4@sha256:cea08321…` and `quay.io/aarchbio/beagle@sha256:7a22203b…` (arm64); `quay.io/biocontainers/minimac4@sha256:cdf67fb7…` and `quay.io/biocontainers/beagle@sha256:503733eb…` (x86) |
