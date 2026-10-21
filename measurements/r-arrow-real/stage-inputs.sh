#!/usr/bin/env bash
# Stage r-arrow-real: 36 NYC TLC yellow-taxi Parquet files (2009, 2010 and 2023; 12.0 GB,
# 378,207,443 trips) and this measurement's scripts.
#
#   bash measurements/r-arrow-real/stage-inputs.sh <bucket>
#
# No data is fetched here. The 36 files are the ones two other measurements already pinned and
# staged into the same bucket -- 2009-2010 by measurements/datashader-real (fetched on AWS) and
# 2023 by measurements/r-real -- so this script stages theirs first if they are missing, and
# pins.sha256 is the concatenation of their two pin lists. Tier: a stable public source (the TLC's
# CloudFront origin), because RODA's s3://nyc-tlc refuses every request (measurements/r-real).
# The polars reference (00-reference.task.json) is then run once, and ref.sha256 pins its output.
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/r-arrow-real"

aws s3 ls --region "$REGION" "s3://$BUCKET/inputs/datashader-real/tlc/data.sha256" >/dev/null 2>&1 \
  || bash "$HERE/../datashader-real/stage-inputs.sh" "$BUCKET"
n=$(aws s3 ls --region "$REGION" "s3://$BUCKET/inputs/r-real/tlc/" | grep -c 'yellow_tripdata_2023-..\.parquet' || true)
[ "$n" = 12 ] || bash "$HERE/../r-real/stage-inputs.sh" "$BUCKET"

for f in run.sh analysis.R ref.sh reference.py pins.sha256 ref.sha256; do
  [ -e "$HERE/$f" ] || continue
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
  echo "  $f  $(shasum -a 256 "$HERE/$f" | cut -d' ' -f1)"
done
echo "staged to $DST/"
