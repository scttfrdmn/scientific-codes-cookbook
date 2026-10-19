#!/usr/bin/env bash
# Stage this measurement's one new input, real.py, and check the rest are already in the bucket.
#
#   bash measurements/geo-ml-real/stage-inputs.sh <bucket>
#
# Everything else is read, not copied:
#   tracts    r-spatial-real's 56 TIGER/Line 2024 tract zips, pinned by its tracts.sha256
#   counties  geo-ml's tl_2024_us_county.zip (sha256 04e668d3...)
#   DEM       geospatial-real's 28 Copernicus GLO-30 tiles over Colorado, pinned by its tiles.sha256
#   terra     r-spatial-real's per-tract areas and Colorado zonal table, the cross-code reference,
#             one per chip (its runs/, runs-x86_64/ and runs-amd/ outputs)
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
need() { aws s3 ls --region "$REGION" "s3://$BUCKET/$1" >/dev/null || { echo "missing s3://$BUCKET/$1 -- $2" >&2; exit 1; }; }
need inputs/r-spatial-real/tracts.sha256 "run measurements/r-spatial-real/stage-inputs.sh"
need inputs/geo-ml/tiger.zip "run 'make stage RECIPE=geo-ml'"
need inputs/geospatial-real/tiles.sha256 "run measurements/geospatial-real/stage-inputs.sh"
for p in runs runs-x86_64 runs-amd; do
  need "$p/r-spatial-real/r1/tract-areas.csv" "run measurements/r-spatial-real on that chip first"
  need "$p/r-spatial-real/r1/co-tract-zonal.csv" "run measurements/r-spatial-real on that chip first"
done
aws s3 cp --region "$REGION" --only-show-errors "$HERE/real.py" "s3://$BUCKET/inputs/geo-ml-real/real.py"
echo "real.py  $(shasum -a 256 "$HERE/real.py" | cut -d' ' -f1)  staged to s3://$BUCKET/inputs/geo-ml-real/"
