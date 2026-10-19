#!/usr/bin/env bash
# Stage this measurement's inputs into the cookbook bucket.
#
#   bash measurements/r-spatial-real/stage-inputs.sh <bucket>
#
# Three inputs, one new:
#   tracts    Census TIGER/Line 2024 census tracts, 56 zips (50 states, DC, PR, 4 island areas;
#             ~420 MB). Census's own versioned release; there is no RODA copy. Fetched ON AWS by
#             fetch.task.json (nothing passes through this machine) and pinned by tracts.sha256,
#             the sha256 of each zip as fetched; the run task re-checks every zip in-task.
#   counties  geo-ml's tl_2024_us_county.zip -- read, not copied (same TIGER 2024 vintage)
#   DEM       geospatial-real's 28 Copernicus GLO-30 tiles over Colorado -- read, not copied
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/r-spatial-real"

n="$(aws s3 ls --region "$REGION" "$DST/tract/" | grep -c '_tract\.zip$' || true)"
if [ "$n" != 56 ]; then
  echo "tracts not staged ($n of 56) -- fetch them on AWS first:" >&2
  echo "  sed \"s|\\\${COOKBOOK_BUCKET}|$BUCKET|g\" $HERE/fetch.task.json > fetch.json && spawn task run --spec fetch.json --wait" >&2
  exit 1
fi
# the fetch task's own digests must equal the committed pins
aws s3 cp --region "$REGION" --only-show-errors "$DST/tracts.fetched.sha256" - | diff "$HERE/tracts.sha256" - \
  || { echo "FAIL: the staged tracts are not the pinned bytes" >&2; exit 1; }
echo "56 tract zips staged, digests match tracts.sha256"

aws s3 ls --region "$REGION" "s3://$BUCKET/inputs/geo-ml/tiger.zip" >/dev/null \
  || { echo "counties not staged -- run 'make stage RECIPE=geo-ml' first" >&2; exit 1; }
aws s3 ls --region "$REGION" "s3://$BUCKET/inputs/geospatial-real/tiles.sha256" >/dev/null \
  || { echo "DEM not staged -- run measurements/geospatial-real/stage-inputs.sh first" >&2; exit 1; }

aws s3 cp --region "$REGION" --only-show-errors "$HERE/tracts.sha256" "$DST/tracts.sha256"
aws s3 cp --region "$REGION" --only-show-errors "$HERE/real.R" "$DST/real.R"
echo "real.R  $(shasum -a 256 "$HERE/real.R" | cut -d' ' -f1)"
echo "staged to $DST/"
