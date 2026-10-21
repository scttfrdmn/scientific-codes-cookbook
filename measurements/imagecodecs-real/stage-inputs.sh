#!/usr/bin/env bash
# Stage the scripts. The slides are NOT staged: the task streams each one from RODA
# (s3://camelyon-dataset, same region, public) and checks it against the cohort manifest
# measurements/pathology-real/slides.tsv -- the same 160 annotated slides, the same publisher md5s,
# read from that measurement's staged copy rather than a second one. pins.sha256 pins it here too.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
TSV=../pathology-real/slides.tsv
test "$(($(wc -l < "$TSV") - 1))" = 160 || { echo "slides.tsv must list the 160 annotated slides" >&2; exit 1; }
{ shasum -a 256 real.py run.sh; shasum -a 256 "$TSV" | sed 's|\.\./pathology-real/||'; } > pins.sha256
cat pins.sha256
for f in real.py run.sh pins.sha256; do
  aws s3 cp --only-show-errors --region us-west-2 "$f" "$BUCKET/inputs/imagecodecs-real/$f"
done
# the manifest: stage it under pathology-real's prefix only if it is not already there
aws s3 ls "$BUCKET/inputs/pathology-real/slides.tsv" >/dev/null 2>&1 \
  || aws s3 cp --only-show-errors --region us-west-2 "$TSV" "$BUCKET/inputs/pathology-real/slides.tsv"
echo "staged to $BUCKET/inputs/imagecodecs-real/ (slides.tsv: $BUCKET/inputs/pathology-real/)"
