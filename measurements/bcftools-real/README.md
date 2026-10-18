# bcftools on a real cohort — 1000 Genomes Phase 3, Graviton4 vs x86

> **Every one of 94 identities held on both chips, and every output agreed across them byte for
> byte as text** — 4,044,736 normalised records × 2,504 genotypes. x86 finished 5% sooner and
> Graviton4 cost 9% less per result. bcftools streams: peak RSS was **16–18 MiB** for the whole run.

Workload: chromosomes **20, 21 and 22 of the 1000 Genomes Phase 3 release, all 2,504 samples**
(4,021,926 input records, 775 MB of bgzipped VCF), the size of a per-chromosome job in any cohort
pipeline. One task runs the usual day: `norm -m -any -f` (split + left-align), recompute the
published AC/AN and super-population AFs with `+fill-tags`, `stats`, filter to common SNPs,
strip and restore annotations with `annotate -a`, `concat` the three chromosomes, and split
chr22's cohort by sample and `merge` it back.

## Run it

```sh
export AWS_PROFILE=aws
bash measurements/bcftools-real/stage-inputs.sh "$COOKBOOK_BUCKET"
spawn task run --spec "$(python3 scripts/spec_arch.py bcftools arm64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/bcftools-real/cohort.task.json)" --wait
spawn task run --spec "$(python3 scripts/spec_arch.py bcftools x86_64 "$COOKBOOK_BUCKET" "$(date +%s)" measurements/bcftools-real/cohort.task.json)" --wait
```

The task runs `run.sh` (pinned by sha256) against the staged bytes. The VCFs are the ones
`measurements/plink-real` already staged under `inputs/plink-real/`, so there is one copy.

## Results

4 bcftools threads, one `2xlarge` per chip, same input bytes, the recipe's two pinned images:

| chip | instance | task compute | billed | $/hr | compute $/result | **billed $/result** | peak RSS |
|---|---|---|---|---|---|---|---|
| Graviton4 | `c8g.2xlarge` | 3,750 s | 3,887 s | 0.3190 | 0.3323 | **0.3444** | 16.2 MiB |
| Xeon 6975P-C | `c8i.2xlarge` | 3,553 s | 3,646 s | 0.3748 | 0.3699 | **0.3796** | 18.3 MiB |

A result is the whole verified run: three chromosomes normalised, annotated, filtered,
concatenated and merged, with every check. Per operation, summed over the three chromosomes:

| seconds | norm | fill-tags | stats ×2 | filter | annotate | concat | concat round trip | split + merge | merge check | text census |
|---|---|---|---|---|---|---|---|---|---|---|
| Graviton4 | 256 | 795 | 53 | 145 | 608 | 253 | 637 | 222 | 265 | 265 |
| x86 | 228 | 841 | 43 | 135 | 549 | 224 | 581 | 196 | 237 | 275 |

- **x86 is ~10% faster on the compression-bound steps** (norm, annotate, concat, merge all write
  bgzipped 2,504-sample VCF); **Graviton4 is 5% faster on `+fill-tags`**, which spends its time
  counting genotypes. The hourly price difference (17%) outweighs both, so Graviton4 is cheaper
  per result. The two images link different htslib builds (1.23.1 arm64, 1.24 x86), so a step
  timing is chip *and* htslib, not chip alone; the census column also runs GNU vs BusyBox `awk`.
- **Boot and staging are 2–4% of the bill** (137 s and 93 s): 775 MB stages in 6–7 s in-region.
  This is a compute job, and most of the compute is reading and writing genotype columns, not
  the operation's own logic — `stats` reads the same file in under a twentieth of the time `annotate` takes.
- **Memory is not the constraint; staging is.** bcftools never held more than 18 MiB. The box
  is sized by the tmpfs high-water mark (inputs plus the normalised, concatenated and merged
  copies — ~3.8 GB at most by file size, not measured — of the 8 GiB `/tmp` a 16 GiB instance gives).
- n = 1 per cell. The processing is deterministic integer and text work, and the cross-chip
  outputs are identical (below), so a repeat would only re-measure timing noise.

## What held at scale, on both chips

Every claim is an exact identity; `results/<arch>/checks.tsv` has all 94 with both sides.

| claim | why it must hold | chr20 / chr21 / chr22 |
|---|---|---|
| staged bytes == pinned sha256 | provenance | ok |
| `stats` records and multiallelic sites == a text count of the input (`gzip -dc \| awk`) | independent count | 1,812,841 / 1,105,538 / 1,103,547 records |
| `norm` read every record, split exactly the multiallelic ones, removed nothing | its own counters vs the census | split 8,972 / 6,374 / 6,348; realigned 2,403 / 1,659 / 1,520 |
| records out == ALT alleles in, every record biallelic | one record per allele | 1,822,268 / 1,112,228 / 1,110,240 |
| Σ AC conserved through the split | allele-count conservation | 317,433,531 / 231,209,651 / 204,816,071 |
| AC and AN recomputed from genotypes == the consortium's published tags, **every split record** | published truth; a mis-split genotype would change AC | 0 mismatches |
| super-population AF recomputed (`-S` from the release panel) == published `EAS/AMR/AFR/EUR/SAS_AF` | the README says they are AC/AN per super-population; printed to 4 decimals | max \|diff\| 5e-5 = the print precision |
| `stats` SNPs, MNPs == census; indels + others == census | independent classification | SNPs 1,751,797 / 1,063,228 / 1,064,502 |
| `view -v snps -i 'AF>=0.01'` == an awk count of the same predicate | filter expression | 271,047 / 174,989 / 174,442 |
| strip ID + 3 AF tags, restore with `annotate -a sites.vcf.gz` == original columns (md5) | lossless transfer; controls prove the strip removed them | ok |
| `concat` records == Σ per-chromosome; `view -r` of each chromosome == the input, byte-identical text with all genotypes | lossless round trip | 4,044,736 |
| chr22 split into two 1,252-sample halves, `merge -m none` == every genotype of every record (md5), same sample order | lossless round trip | ok |

Two things the run shows that a fixture would not. **`norm` warns that `INFO/SVLEN` is
`Number=.`** in the release header, so it cannot split that field on multiallelic SV records. And **bcftools files 22 / 19 / 21 non-symbolic alleles as
"other", not indel** — complex replacements with no shared base, like `A → TT` or `ATAATA → T` at
chr20:8208842 — so a check that counts indels by length disagrees with `stats` by exactly those
records (listed in `run.log`, and asserted to be the whole difference).

## Across chips: all of it is a fixed point

The only floating point here is parsing and dividing allele frequencies, so nothing is a path. Every value in `results.tsv` and every left-hand side in `checks.tsv` — including the md5s
of the restored annotation columns, the concat round trip's full record text and the merged
genotype matrix — is **identical across chips**. The `stats` files differ only in their first
line (`1.24+htslib-1.23.1` vs `1.24+htslib-1.24`); the bgzipped outputs differ in size by 2 bytes, the
length difference of that version string in their header (the compressed files were not
compared further; their record text was, above).

## Pins

| | |
|---|---|
| VCFs | `s3://1000genomes/release/20130502/ALL.chr{20,21,22}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz` (RODA), sha256 `4a0b61d6…`, `086fc3a2…`, `5d52145d…`; read from `inputs/plink-real/` |
| panel | `integrated_call_samples_v3.20130502.ALL.panel` (RODA, same release), sha256 `b4023dc6…` |
| reference | built: contigs 20–22 cut verbatim from RODA `technical/reference/phase2_reference_assembly_sequence/hs37d5.fa.gz` (sha256 `2555eae3…`, the assembly Phase 3 was called on) → `hs37d5.chr20-22.fa`, sha256 `13cf156f…` |
| script | `run.sh`, sha256 `5bf93080…` (checked in-task against `pins.sha256`) |
| images | the recipe's `quay.io/aarchbio/bcftools@sha256:8171fe74…` (arm64) and `quay.io/biocontainers/bcftools@sha256:186c45de…` (x86), both 1.24 |
