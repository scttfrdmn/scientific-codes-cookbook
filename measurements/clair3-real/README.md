# Clair3 on a whole chromosome of HG002 from PacBio HiFi, scored against GIAB v4.2.1 by stratum, three chips

> **Clair3 2.0.3 calls all of HG002's chr20 from 49× PacBio HiFi Revio reads. RTG vcfeval scores
> the calls against GIAB's v4.2.1 benchmark, stratified by GIAB's v3.3 regions.** SNV F1 is
> 0.99961. Indel F1 is 0.96682, which **failed the all-region floor of 0.98 set before any run**; the
> guard was then moved to GIAB's easy stratum, a bound changed after seeing data, stated here. Every indel error sits in a tandem repeat or homopolymer, and in
> GIAB's easy regions all 3,387 truth indels are called with zero false positives. Every site,
> allele, genotype and FILTER is identical on Graviton4, Intel and AMD, so every score is
> identical too. Only the network's QUAL and GQ move, in the last digit. AMD is fastest (2.0×
> Graviton4) and cheapest per result. Each chip ran for 7 to 9 cents.

Workload: **all of chr20 for one sample, at 49×.** A Clair3 whole-genome job parallelises by
contig and chunk, so a chromosome is one honest unit of it, about 2% of the genome. chr20 is also
the chromosome Clair3's authors hold out of training for every model ("Chr20 was excluded from
all training", [Clair3 README](https://github.com/HKU-BAL/Clair3)), and `hifi_revio` was trained
on HG002 and HG004. So this is the held-out test their own models were built for, not a sample
the network has already seen.
- **Reads:** the GIAB PacBio HiFi Revio 48× BAM (`HG002_PacBio-HiFi-Revio_20231031_48x_GRCh38-GIABv3.bam`),
  cut to chr20 by [phasing-real](../phasing-real/README.md): 191,592 reads, mean 16.7 kb, 49.2×.
  It was cut over HTTPS through GIAB's md5-checked index. Reused, not re-staged.
- **Truth:** GIAB NISTv4.2.1 GRCh38, `HG002_GRCh38_1_22_v4.2.1_benchmark.vcf.gz` chr20 (phasing-real's
  pinned slice, 85,951 records). Its confident regions are `…_benchmark_noinconsistent.bed`
  (56.9 Mb on chr20).
- **Strata:** GIAB genome-stratifications v3.3 GRCh38, eight BEDs. Each is md5-checked against
  GIAB's `GRCh38-genome-stratifications-md5s.txt` at staging.
- **Model:** `hifi_revio`, bundled in both images. No download.

**Data tier: RODA.** The BED and the strata are copied server-side from `s3://giab` by
[`stage-inputs.sh`](stage-inputs.sh). Every staged file, scripts included, is pinned in
[`pins.sha256`](pins.sha256), and each task checks its pins before it runs.

## Run it

```sh
export AWS_PROFILE=aws B=<your bucket>
bash measurements/clair3-real/stage-inputs.sh "$B"     # needs phasing-real staged first
N=$(date +%s)
for a in arm64 x86_64 amd; do
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py clair3 $a "$B" "$N" measurements/clair3-real/01-call.task.json)"
done
for t in 02-eval 03-strat; do   # Graviton4, once, over all three VCFs
  spawn task run --wait --spec "$(python3 scripts/spec_arch.py clair3 arm64 "$B" "$N" measurements/clair3-real/$t.task.json)"
done
bash measurements/clair3-real/collect.sh "$B" "$N" "$N"   # results/, results/perchip.txt
```

[`call.sh`](call.sh) is the recipe's invocation with the HiFi model:

```sh
run_clair3.sh --bam_fn=hifi.chr20.bam --ref_fn=chr20.fa --threads=16 --platform=hifi \
  --model_path="$(dirname "$(command -v run_clair3.sh)")/models/hifi_revio" --ctg_name=chr20 --output=clair3_out
```

[`eval.sh`](eval.sh) runs `rtg vcfeval -b truth -c clair3.vcf.gz -t chr20.sdf -e bench.bed`, which
scores PASS calls only, with no threshold. That is hap.py's comparison engine, used the way
GIAB's benchmarking uses it. [`strat.sh`](strat.sh) then counts vcfeval's TP/FP/FN records in
each stratum, as hap.py does. The specs come from [`mkspec.py`](mkspec.py).

## Results

One `c8*.4xlarge` (16 vCPU, 32 GiB) per chip, `--threads=16`, same staged bytes. The x86 legs run
bioconda's Clair3 2.0.3, the recipe's `images_x86_64`:

| chip | instance | Clair3 wall | billed | peak RSS | $/hr | compute $/result | **billed $/result** |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.4xlarge` | 411 s | 513 s | 13.7 GiB | 0.6381 | 0.0728 | **0.0909** |
| Xeon 6975P-C | `c8i.4xlarge` | 274 s | 435 s | 11.7 GiB | 0.7497 | 0.0571 | **0.0906** |
| **EPYC 9R45** | `c8a.4xlarge` | **205 s** | **290 s** | 13.2 GiB | 0.8622 | **0.0491** | **0.0695** |

- *Clair3 wall* is `run_clair3.sh` end to end: pileup calling, WhatsHap phasing, haplotagging,
  full-alignment calling and merge. *Billed* runs from launch to terminate, from spawn's
  completion record. $/hr is spawn's price for the instance. Peak RSS is the container's cgroup
  `memory.peak`, which covers every Clair3 worker process. A result is the whole chromosome called.
- Scoring is one more step: `02-eval` (`m8g.large`, 5 s per VCF) plus `03-strat` (`m8g.large`)
  cost $0.0042 together for all three VCFs.
- **AMD is 2.0× Graviton4 and 24% cheaper per result billed.** Intel is 1.5× Graviton4 and costs
  the same per result billed. Its 16 vCPU are 8 cores with SMT (2 threads per core; 1 on
  Graviton4 and AMD).
- **The x86 lead is a different numerics stack, not only a different chip.** Both images ship
  PyTorch 2.13.0, but the arm64 build runs SVE128 kernels over OpenBLAS (`neoversev2`), and the
  x86 build runs AVX512 kernels over MKL. This is a measurement of the two builds a researcher
  actually gets from conda, not of the silicon alone.
- **Sized from a measured run.** A first Graviton4 run on `m8g.4xlarge` (64 GiB) took 412 s and
  peaked at 13.7 GiB, so the box moved to `c8g.4xlarge`. That run's identical 411 s shows memory
  was never the limit. Staging is 1.5 GB of tmpfs, against 16 GiB available.
- **Overhead is 20–37% of each bill**: boot, Docker install, the image pull and 1.5 GB of staging.
  These timings are not compute cost.
- n = 1 per chip. The call set is identical on all three chips, so a repeat would only
  re-measure timing.

## What held at scale, on all three chips

| claim | why it must hold | Graviton4 | Intel | AMD |
|---|---|---|---|---|
| sha256 of every staged input == pins; every stratum's md5 == GIAB's | provenance | ok | ok | ok |
| `hifi_revio` bundled; `python3` ELF `e_machine` == `uname -m` | right model, not emulated | 183 | 62 | 62 |
| one sample, 0 records off chr20, 0 PASS rows without an ALT allele | structure | ok | ok | ok |
| records == PASS + LowQual + RefCall; no other FILTER | Clair3's FILTER vocabulary | 138,794 = 137,399 + 1,395 + 0 | same | same |
| vcfeval's summary TP-base / TP-call / FP / FN == records in its output VCFs | the scorer counts what it wrote | 82,221 / 82,251 / 438 / 368 | same | same |
| TP-call + FP == PASS calls overlapping the benchmark regions | every in-region call is scored once | 82,689 | same | same |
| TP-base + FN == truth ALT records overlapping the benchmark regions | every truth variant is scored once | 82,589 | same | same |
| easy + all-difficult == all, and not-lowmap/segdup + lowmap/segdup == all, for all 8 counts | a stratum and its complement partition the genome | 32 / 32 | 32 / 32 | 32 / 32 |
| truth totals per stratum and type identical across chips | the truth set does not depend on the caller | — | 18 / 18 | 18 / 18 |
| SNV F1 ≥ 0.995 (all regions), ≥ 0.999 (easy) | broken-run guard | 0.99961 / 0.99995 | same | same |
| indel F1 ≥ 0.99 in GIAB's easy regions | broken-run guard where long reads should reach the truth | 1.00000 | same | same |

109 checks in `03-strat` (`results/strat/checks.tsv`) and 9 per call task, 0 failed. Membership
in a stratum is decided by POS (`bcftools -T`), which makes complementary strata an exact
partition. The region totals use record overlap (`--targets-overlap record`), because that is
the rule vcfeval itself uses: by POS, one record on each side falls just outside.

**Indel F1 over all regions is reported, not asserted. An earlier floor of 0.98 failed at 0.96682
on every chip.** That floor was set before any run, from a guess at Clair3's published HiFi
numbers, not from anything this chromosome must satisfy. The strata show where the deficit is:
all 432 false-positive indels and 315 of 318 false negatives lie in
`AllTandemRepeatsandHomopolymers_slop5`. In GIAB's easy regions indels are perfect. vcfeval's
best GQ threshold (7) lifts indel F1 only to 0.96921 (`non_snp_roc.tsv.gz`), so no quality
cutoff removes the problem. The guard moved to the easy stratum, where a broken run would show,
and the all-region number stands as the finding below.

### Scores against the truth (`results/strat/stratified.tsv`, identical on all three chips)

| stratum (chr20 ∩ benchmark) | SNV truth | SNV recall | SNV precision | **SNV F1** | indel truth | indel recall | indel precision | **indel F1** |
|---|---|---|---|---|---|---|---|---|
| all | 71,333 | 0.99930 | 0.99992 | **0.99961** | 11,256 | 0.97175 | 0.96195 | **0.96682** |
| easy (not in all difficult) | 60,394 | 0.99992 | 0.99998 | 0.99995 | 3,387 | 1.00000 | 1.00000 | 1.00000 |
| all difficult | 10,939 | 0.99589 | 0.99954 | 0.99771 | 7,869 | 0.95959 | 0.94578 | 0.95263 |
| tandem repeats + homopolymers | 3,970 | 0.99824 | 0.99875 | 0.99849 | 7,425 | 0.95758 | 0.94265 | 0.95006 |
| low mappability + segdups | 3,804 | 0.99290 | 1.00000 | 0.99644 | 269 | 0.99257 | 1.00000 | 0.99627 |
| segdups | 1,510 | 0.98212 | 1.00000 | 0.99098 | 142 | 0.98592 | 1.00000 | 0.99291 |
| low mappability | 2,723 | 0.99008 | 1.00000 | 0.99502 | 149 | 0.98658 | 1.00000 | 0.99324 |
| HG002 complex variants + SVs | 6,624 | 0.99925 | 0.99985 | 0.99955 | 1,557 | 0.96146 | 0.96119 | 0.96133 |

Observed, not asserted:
- **HiFi solves mappability, and repeats are what's left.** In segdups and low-mappability
  regions Clair3 makes **zero** false positives of either type, and recall stays above 0.98. Those
  are the regions where short reads fail. What remains is tandem-repeat and homopolymer indel
  length. Of the 438 false positives, 410 change the length of an A or T run and 358 carry an
  insertion. Their median allele fraction is 0.29 and their median QUAL is 7.9. 162 are `1/2`
  calls, two lengths of one repeat.
- **SNVs are at the level of the truth.** Over 60,394 easy-region SNVs there are 5 FN and 1 FP.
- **This BAM has no RefCall rows.** The recipe's Illumina run writes a RefCall row for every
  position judged reference (179,453 on NA12878's chr20). The HiFi run on HG002 writes PASS and
  LowQual only. `FILTER=PASS` is still the right gate, because vcfeval ignores non-PASS records.

## Across chips: what is a fixed point and what is a path

**Every decision is a fixed point** (`results/strat/results.tsv`):
- PASS sites, alleles, genotypes and FILTERs: 0 differing rows, Intel and AMD each against
  Graviton4 (`vcf_sites_md5` `34fd0b44…` on all three);
- LowQual count (1,395), every vcfeval TP-base/FN/FP record, every stratified count and score.

**The network's scores are a path:**
- QUAL or GQ differs on **286** of 138,794 rows on Intel and on **332** on AMD, against Graviton4.
  Every difference is in the last printed digit (`23.97` vs `23.98`). Two TP records differ in GQ
  alone. None moves a call across Clair3's PASS threshold or vcfeval's matching, so the VCF bodies
  differ (md5 `eebf3c95…` / `3246197b…` / `7baa583f…`) while the call sets do not.
- The two x86 chips also differ from each other: same image digest, both on MKL, different
  VCF-body md5. So there are three sets of QUAL values and one set of calls.

That is the recipe's NA12878 Illumina finding (184 QUALs moved, no decision changed) at a larger
scale, on a second platform and a third chip.

## Caveats

- **Scorer provenance.** No arm64 image of RTG Tools or hap.py exists. hap.py is linux-64 only on
  bioconda. `rtg-tools` 3.13 is `noarch`, but aarch.bio has not built it. So `02-eval` runs
  `RTG.jar`, extracted from bioconda's own `rtg-tools-3.13-hdfd78af_0.tar.bz2`
  (sha256 `3af3c579…`, as anaconda.org publishes it), under the JVM of `recipes/picard`'s pinned
  `quay.io/aarchbio/picard@sha256:c6a742e8…` (OpenJDK 25). That is the same jar that bioconda's
  x86 `rtg-tools` image carries. One scorer scores all three VCFs, so a cross-chip score
  difference could only come from the calls.
- Indels are scored at vcfeval's haplotype level, which credits equivalent representations. The
  recipe's `bcftools isec` scorer cannot, so its indel numbers are not comparable to these.
- One chromosome of one sample. A whole-genome F1 would be weighted by the genome's own
  stratum mix, so expect the all-region indel F1 to move with its tandem-repeat share.
