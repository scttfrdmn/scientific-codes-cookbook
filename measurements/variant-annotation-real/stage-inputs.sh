#!/usr/bin/env bash
# Stage the variant-annotation-real inputs.
#
#   sites       1000 Genomes Phase 3 chr20-22, all 2,504 samples (RODA s3://1000genomes/release/20130502),
#               already in the bucket under inputs/plink-real/ -- shared, not copied
#   reference   hs37d5 chr20-22, cut verbatim from RODA hs37d5.fa.gz by measurements/bcftools-real,
#               already in the bucket under inputs/bcftools-real/ -- shared, not copied
#   annotation  Ensembl GRCh37 release 87 (the release GRCh37's gene set froze at): chromosome 20-22
#               GFF3, and the release's CDS and peptide FASTA. Fetched by 00-stage on a spawn host
#               and checked there against Ensembl's own CHECKSUMS; nothing passes through this
#               machine. Their sha256 are in pins.sha256.
#   scripts     csq.sh, snpeff.sh, vep.sh, lib.sh, pairs.awk, compare.awk -- every task checks its
#               staged inputs, scripts included, against pins.sha256 before it runs.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/variant-annotation-real"

for f in inputs/bcftools-real/hs37d5.chr20-22.fa \
         inputs/plink-real/ALL.chr22.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz; do
  aws s3 ls --region "$REGION" "$BUCKET/$f" > /dev/null \
    || { echo "missing $f -- stage measurements/bcftools-real (and plink-real) first" >&2; exit 1; }
done

aws s3 cp --only-show-errors --region "$REGION" "$HERE/fetch-ensembl.sh" "$P/fetch-ensembl.sh"
if ! aws s3 ls --region "$REGION" "$P/ensembl.sha256" > /dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" variant-annotation arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-stage.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
# what 00-stage measured must be what pins.sha256 pins
aws s3 cp --only-show-errors --region "$REGION" "$P/ensembl.sha256" - | while read -r h f; do
  grep -q "^$h  $f\$" "$HERE/pins.sha256" || { echo "staged $f ($h) is not the pinned file" >&2; exit 1; }
done

( cd "$HERE" && grep -E ' (csq\.sh|snpeff\.sh|vep\.sh|lib\.sh|pairs\.awk|compare\.awk)$' pins.sha256 | shasum -a 256 -c - )
for f in csq.sh snpeff.sh vep.sh lib.sh pairs.awk compare.awk pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
echo "staged $P/"
