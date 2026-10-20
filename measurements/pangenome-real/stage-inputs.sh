#!/usr/bin/env bash
# Stage the pangenome-real inputs.
#
#   graph    HPRC v1.1 minigraph-cactus GRCh38, chr20, the d9 (Giraffe) graph: a server-side copy
#            of one RODA object (s3://human-pangenomics, us-west-2), pinned by its S3 version id
#            here and by sha256 in pins.sha256
#   reads    HG002 Illumina 2x250 chr20, already in the bucket from measurements/phasing-real
#   ref      GRCh38 chr20, already in the bucket from recipes/bwa-samtools
#   scripts  lib.sh, prep.sh, giraffe.sh -- every task checks its staged inputs, scripts included,
#            against pins.sha256 before it runs
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="inputs/pangenome-real"
SRC=human-pangenomics/pangenomes/freeze/freeze1/minigraph-cactus/hprc-v1.1-mc-grch38/hprc-v1.1-mc-grch38.chroms/chr20.d9.vg
VERSION=0wTd0vbZPssWXgkhAVEc94YeX8C1DHHo

for f in inputs/bwa-samtools/chr20.fa inputs/phasing-real/prep/ilmn.chr20.bam; do
  aws s3 ls --region "$REGION" "s3://$BUCKET/$f" > /dev/null \
    || { echo "missing s3://$BUCKET/$f -- stage recipes/bwa-samtools and measurements/phasing-real first" >&2; exit 1; }
done
aws s3 ls --region "$REGION" "s3://$BUCKET/$P/hprc/chr20.d9.vg" > /dev/null 2>&1 \
  || aws s3api copy-object --region "$REGION" --copy-source "$SRC?versionId=$VERSION" \
       --bucket "$BUCKET" --key "$P/hprc/chr20.d9.vg" > /dev/null

( cd "$HERE" && grep -E ' (lib\.sh|prep\.sh|giraffe\.sh)$' pins.sha256 | shasum -a 256 -c - )
for f in lib.sh prep.sh giraffe.sh pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "s3://$BUCKET/$P/$f"
done

if ! aws s3 ls --region "$REGION" "s3://$BUCKET/$P/prep/prep.sha256" > /dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" pangenome arm64 "$BUCKET" "$(date +%s)" "$HERE/00-prep.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
# what 00-prep wrote and hashed must be what pins.sha256 pins
aws s3 cp --only-show-errors --region "$REGION" "s3://$BUCKET/$P/prep/prep.sha256" - | while read -r h f; do
  grep -q "^$h  $f\$" "$HERE/pins.sha256" || { echo "staged $f ($h) is not the pinned file" >&2; exit 1; }
done
echo "staged s3://$BUCKET/$P/"
