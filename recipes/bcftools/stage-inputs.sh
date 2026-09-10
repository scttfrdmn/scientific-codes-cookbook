#!/usr/bin/env bash
# Stage the SHARED high-coverage fixture the variant-caller recipes need.
#
# Why this exists: the genomics recipes' original subsample (recipes/bwa-samtools'
# 400k read pairs over all of chr20) is ~0.3x depth — plenty for *alignment*
# identities, but below the regime where *variant calling* means anything. Two
# correct callers disagree at 0.3x for a reason that says nothing about either
# caller, so a bcftools<->freebayes concordance on that input would be measuring a
# broken premise, not agreement (CLAUDE.md: a cross-code check must compare like
# with like). This fixture puts the comparison at ~30x, where confident concordance
# is a genuine check.
#
# REUSABLE, deliberately: a 30x region is not a one-off for this pair. It unlocks
# GATK (the genomics row still documented not-ready), any future caller comparison,
# and depth-sensitive tools generally — so it lives under inputs/highcov/, not under
# one recipe's prefix.
#
# Region chosen deliberately: chr20:2,000,000-2,400,000 is euchromatic p-arm, well
# clear of the ~26-28 Mb centromere and both telomeres, and measures clean — mean
# depth 35.2x with only 19 zero-coverage bases and 78 under-10x of 400,001, so the
# callers exercise normal calling, not repeat/coverage edge cases.
#
# Deterministic + pinnable: `samtools view -b` of a fixed region from an immutable
# CRAM, with a pinned samtools image, reproduces the same bytes — so the output
# sha256 below is a real pin, not a moving target.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
PREFIX="inputs/highcov"
REGION="chr20:2000000-2400000"
# HG00096, 1000G NYGC 30x resequencing (ERP114329). ERR3240114 from the release's
# own sequence.index. Aligned to GRCh38_full_analysis_set_plus_decoy_hla
# (chr20 M5 b18e6c531b0bd70e949a7fc20859cb01).
CRAM="https://1000genomes.s3.amazonaws.com/1000G_2504_high_coverage/data/ERR3240114/HG00096.final.cram"
SIMG="quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# CRAM region query range-fetches only the needed slices (~few MB, not the 15.7 GB
# whole); the reference for decode comes from the EBI CRAM MD5 registry by M5 tag.
docker run --rm --platform linux/arm64 --user "$(id -u):$(id -g)" -v "$WORK:/d" -w /d \
  --entrypoint bash "$SIMG" -c "
    export REF_PATH='https://www.ebi.ac.uk/ena/cram/md5/%s' REF_CACHE=/d/rc
    samtools view -b -h '$CRAM' '$REGION' -o region.bam
    samtools index region.bam
    samtools depth -a -r '$REGION' region.bam | \
      awk '{s+=\$3;n++} END{printf \"mean_depth=%.1f over %d bp\n\", s/n, n}'
  "

# Expected pins (record in the READMEs; the recipes re-verify the BAM's sha256 on the box):
#   region.bam      sha256 6949939b5937046f1ec7fdcc764dc47df5dd3c35d48b5487470ae3527d21b04a
#   region.bam.bai  sha256 657150daff90fba5aa620d81d7b3691b00f4cc76aabc1c8f2c73a9d690537e22
shasum -a 256 "$WORK/region.bam" "$WORK/region.bam.bai" 2>/dev/null || sha256sum "$WORK/region.bam" "$WORK/region.bam.bai"

aws s3 cp "$WORK/region.bam"     "s3://$BUCKET/$PREFIX/HG00096.chr20_2.0-2.4Mb.30x.bam"     --only-show-errors
aws s3 cp "$WORK/region.bam.bai" "s3://$BUCKET/$PREFIX/HG00096.chr20_2.0-2.4Mb.30x.bam.bai" --only-show-errors
echo "staged s3://$BUCKET/$PREFIX/HG00096.chr20_2.0-2.4Mb.30x.bam (+ .bai)"
