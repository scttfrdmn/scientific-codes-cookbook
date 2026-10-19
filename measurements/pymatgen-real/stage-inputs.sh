#!/usr/bin/env bash
# Stage measurements/pymatgen-real: the Materials Project 2025-09-25 release from RODA
# (s3://materialsproject-build, us-east-1) -- the summary collection (every material, with its
# structure and MP's symmetry) and the thermo collection (every thermo doc, with its entries and
# MP's hull results) -- copied S3-to-S3 into your bucket, plus this directory's scripts.
# Nothing touches the local disk. The task re-checks every staged file against inputs.sha256.
#   stage-inputs.sh <bucket> [scripts]     (scripts: re-stage only this directory's scripts)
set -euo pipefail
B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
SRC=s3://materialsproject-build/collections/2025-09-25
DST="s3://$B/inputs/pymatgen-real"
if [ "${2:-}" != scripts ]; then
aws s3 cp "$SRC/summary/" "$DST/mp-2025-09-25/summary/" --recursive \
  --source-region us-east-1 --region "$REGION" --only-show-errors
aws s3 cp "$SRC/thermo/" "$DST/mp-2025-09-25/thermo/" --recursive --exclude 'manifest*' \
  --source-region us-east-1 --region "$REGION" --only-show-errors
fi
for f in run.sh real.py inputs.sha256; do
  [ -f "$D/$f" ] && aws s3 cp "$D/$f" "$DST/$f" --region "$REGION" --only-show-errors
done
echo "staged $DST/"
