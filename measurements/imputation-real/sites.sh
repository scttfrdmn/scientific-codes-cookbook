#!/usr/bin/env bash
# Task 00 (minimac4 image, run once): the genotyping-array sites. The 1000 Genomes consortium's own
# Illumina Omni2.5 genotypes of its samples (RODA, supporting/hd_genotype_chip/) carry the real
# array's site list; only its chr20 CHROM/POS/REF/ALT are kept. The 1.3 GB source is hashed here,
# on AWS, and the hash is written beside the small derived file that the other tasks pin.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
OMNI=ALL.chip.omni_broad_sanger_combined.20140818.snps.genotypes.vcf.gz
mkdir -p "$O/sites"
sha256sum "$OMNI" "$OMNI.tbi" | tee "$O/sites/omni.sha256"
rec bcftools_version "$(bcftools --version | head -1)"
bcftools query -r 20 -f '%CHROM\t%POS\t%REF\t%ALT\n' "$OMNI" > "$O/sites/omni.chr20.sites.tsv" 2> "$O/sites/query.log"
RC=$?
chk "bcftools query exit status" "$RC" 0
rec omni_chr20_records "$(wc -l < "$O/sites/omni.chr20.sites.tsv" | tr -d ' ')"
rec omni_chr20_biallelic_acgt "$(awk -F'\t' '$3~/^[ACGT]$/ && $4~/^[ACGT]$/' "$O/sites/omni.chr20.sites.tsv" | wc -l | tr -d ' ')"
sha256sum "$O/sites/omni.chr20.sites.tsv" | sed 's|/tmp/out/sites/||' | tee -a "$O/sites/omni.sha256"
mv "$O/results.tsv" "$O/checks.tsv" "$O/sites/"
[ "$FAILS" = 0 ] && echo "SITES OK" || { echo "SITES FAILED"; exit 1; }
