#!/usr/bin/env bash
# Stage the Kozich et al. 2013 MiSeq "stability" study and the mock-community reference for dada2-real.
#
# DATA TIER: a stable public source. The Schloss lab distributes the paper's data from its own S3
# bucket (s3://mothur, us-east-2; linked from mothur.org/MiSeqDevelopmentData and the DADA2
# tutorial). The copy is server-side, S3 to S3 -- nothing is downloaded locally. The objects are
# pinned by sha256, which the task checks on AWS before it reads them.
#
#   StabilityNoMetaG.tar  the whole 2x250 V4 study: every mouse-gut sample and the HMP mocks
#   miseqsopdata.zip      the DADA2 tutorial's archive; carries HMP_MOCK.v35.fasta, the mock's truth
#
# The R script is staged as a pinned file, not inlined (EC2 user data is 16 KiB, spawn#1063).
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DST="$BUCKET/inputs/dada2-real"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC=s3://mothur
for k in data/MiSeqDevelopmentData/StabilityNoMetaG.tar wiki/miseqsopdata.zip; do
  aws s3 cp "$SRC/$k" "$DST/${k##*/}" --source-region us-east-2 --region "$REGION" --copy-props none --only-show-errors
done
for f in dada2-real.R run.sh pins.sha256; do
  if [ -f "$HERE/$f" ]; then
    aws s3 cp "$HERE/$f" "$DST/$f" --region "$REGION" --only-show-errors
    shasum -a 256 "$HERE/$f"
  fi
done
aws s3 ls "$DST/" --region "$REGION"
