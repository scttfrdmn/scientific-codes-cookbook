#!/usr/bin/env bash
# Stage the imputation-real inputs.
#
#   cohort   1000 Genomes Phase 3 chr20, all 2,504 samples (RODA s3://1000genomes/release/20130502),
#            already in the bucket under inputs/plink-real/ -- shared, not copied
#   array    the consortium's Illumina Omni2.5 genotypes of its own samples (RODA,
#            release/20130502/supporting/hd_genotype_chip/), copied server-side; 00-sites hashes it
#            on AWS and keeps the chr20 site list, which is what the other tasks pin
#   scripts  lib.sh, sites.sh, prep.sh, beagle.sh, mm4.sh, score.py, vcfdiff.py -- every task checks its staged
#            inputs, scripts included, against pins.sha256 before it runs
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/imputation-real"
CHIP=s3://1000genomes/release/20130502/supporting/hd_genotype_chip
OMNI=ALL.chip.omni_broad_sanger_combined.20140818.snps.genotypes.vcf.gz

aws s3 ls --region "$REGION" "$BUCKET/inputs/plink-real/ALL.chr20.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz" > /dev/null \
  || { echo "missing chr20 -- stage measurements/plink-real first" >&2; exit 1; }

( cd "$HERE" && grep -E ' (lib\.sh|sites\.sh|prep\.sh|beagle\.sh|mm4\.sh|score\.py|vcfdiff\.py)$' pins.sha256 | shasum -a 256 -c - )
for f in lib.sh sites.sh prep.sh beagle.sh mm4.sh score.py vcfdiff.py pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done

if ! aws s3 ls --region "$REGION" "$P/omni.sha256" > /dev/null 2>&1; then
  for f in "$OMNI" "$OMNI.tbi"; do
    aws s3 cp --only-show-errors --source-region us-east-1 --region "$REGION" "$CHIP/$f" "$P/$f"
  done
  spec=$(python3 "$ROOT/scripts/spec_arch.py" imputation arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-sites.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
# what 00-sites measured must be what pins.sha256 pins
aws s3 cp --only-show-errors --region "$REGION" "$P/omni.sha256" - | while read -r h f; do
  grep -q "^$h  $f\$" "$HERE/pins.sha256" || { echo "staged $f ($h) is not the pinned file" >&2; exit 1; }
done
echo "staged $P/"
