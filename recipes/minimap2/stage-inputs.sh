#!/usr/bin/env bash
# Stage minimap2's long reads: chr20 of NA12878's published PacBio HiFi deposit, converted back
# to FASTQ so minimap2 performs a real alignment instead of inheriting pbmm2's placements.
#
# This runs as a spawn TASK, not locally: the source BAM is 58.4 GB and the chr20 slice plus the
# FASTQ conversion is ~1.8 GB out, which is not something to pull through a home uplink.
# Extracting a pinned public input is staging, so it belongs here — it just executes on the box.
#
# The reference (chr20.fa) and the GIAB truth set are staged by bwa-samtools and gatk4; this
# recipe reads THOSE bytes so long- and short-read results are anchored to identical input.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
REGION="${AWS_REGION:-us-west-2}"
SPEC="$(dirname "$0")/00-prep-reads.task.json"
DST="s3://$BUCKET/inputs/minimap2/hifi.chr20.fq.gz"

if aws s3 ls "$DST" --region "$REGION" >/dev/null 2>&1; then
  echo "HiFi reads already staged: $DST"
else
  echo "extracting chr20 HiFi reads on m8g.2xlarge (~3.5 min, ~\$0.02, self-terminating)..."
  tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
  sed "s|\${COOKBOOK_BUCKET}|$BUCKET|g" "$SPEC" > "$tmp"
  spawn task run --spec "$tmp" --region "$REGION" --wait
fi

echo "staged:"
echo "  $DST   (217,281 reads, 2,159,335,308 bases, mean 9,938 bp, 33.5x on chr20)"
echo "reference and GIAB truth come from 'make stage RECIPE=bwa-samtools' and 'RECIPE=gatk4'."
