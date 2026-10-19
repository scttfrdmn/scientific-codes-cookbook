#!/usr/bin/env bash
# Stage the scripts and the cohort manifest. The slides are NOT staged: the task fetches each one
# from RODA (s3://camelyon-dataset, same region, public) and checks it against slides.tsv, whose
# md5s come from the publisher's checksums.md5 (tissue masks: S3 single-part ETag). Rebuild
# slides.tsv with make_slides.py from the two manifests the recipe pins by sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
test "$(($(wc -l < slides.tsv) - 1))" = 160 || { echo "slides.tsv must list the 160 annotated slides" >&2; exit 1; }
shasum -a 256 real.py run.sh slides.tsv > pins.sha256
cat pins.sha256
for f in real.py run.sh slides.tsv pins.sha256; do
  aws s3 cp --only-show-errors --region us-west-2 "$f" "$BUCKET/inputs/pathology-real/$f"
done
echo "staged to $BUCKET/inputs/pathology-real/"
