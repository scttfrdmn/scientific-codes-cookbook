#!/usr/bin/env bash
# Stage the polish-real reads (RODA .sra, server-side), scripts and pins. 00-prep converts the .sra
# on AWS; 00-fetch downloads figshare's assemblies and medaka's model on AWS, each checked against
# its publisher's checksum. Nothing is downloaded to the machine running this.
#
#   bash stage-inputs.sh <bucket>
#
# pins.sha256 holds the scripts (rewritten here) and the data and derived objects (kept as
# recorded by the runs that produced them: fetched/, then the draft and the overlaps).
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/polish-real"
SCRIPTS="common.sh run.py prep.sh fetch.sh draft.sh overlap.sh racon.sh medaka.sh score.py"
cd "$HERE"
# The reads: the AWS Open Data SRA mirror's .sra, copied server-side (us-east-1 -> the bucket).
aws s3 ls --region "$REGION" "$P/ERR12813217.sra" >/dev/null 2>&1 || aws s3 cp --only-show-errors \
  --source-region us-east-1 --region "$REGION" s3://sra-pub-run-odp/sra/ERR12813217/ERR12813217 "$P/ERR12813217.sra"
{
  shasum -a 256 $SCRIPTS
  grep -v -E " ($(echo $SCRIPTS | sed 's/\./\\./g; s/ /|/g'))$" pins.sha256 2>/dev/null || true
} > pins.new
mv pins.new pins.sha256
for f in $SCRIPTS pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
echo "staged under $P/ -- every task checks its inputs against pins.sha256"
