#!/usr/bin/env bash
# Stage kraken2-real. Nothing new is downloaded: the reads are the two whole ZymoBIOMICS runs
# metaphlan-real already staged from RODA (Nicholls et al. 2019, PRJEB29504 -- ERR2984773 D6300
# even, ERR2935805 D6310 log), and the index is the recipe's standard-8. This checks both are in
# the bucket and uploads the scripts. Every object the task reads is pinned in pins.sha256 and
# verified on the box before kraken2 runs.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"

aws s3 ls --region "$REGION" "$BUCKET/inputs/kraken2/k2_standard_08gb_20250402.tar.gz" >/dev/null \
  || { echo "stage the recipe first: make stage RECIPE=kraken2" >&2; exit 1; }
aws s3 ls --region "$REGION" "$BUCKET/inputs/metaphlan-real/prep/ERR2935805/ERR2935805_2.fastq.gz" >/dev/null \
  || { echo "stage the reads first: bash measurements/metaphlan-real/stage-inputs.sh <bucket>" >&2; exit 1; }
for f in classify.py score.py zymo.tsv pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$BUCKET/inputs/kraken2-real/$f"
done
echo "staged scripts under $BUCKET/inputs/kraken2-real/"
