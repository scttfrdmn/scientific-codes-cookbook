#!/usr/bin/env bash
# Stage the earth-observation-real references and scripts. The pixels are NOT copied.
#
#   bash measurements/earth-observation-real/stage-inputs.sh <bucket>
#
# Data: every 2024 Sentinel-2 L2A acquisition of MGRS tile 11SKA (central California) in the AWS
# Open Data `sentinel-cogs` bucket (RODA, us-west-2), listed in scenes.txt: 153 items. A 154th,
# S2B_11SKA_20241025_0_L2A, is 100% nodata and publishes no COGs, so it is not in the list.
#
# What moves, and what does not:
#   items/<id>.json  the STAC items, which carry ESA's published class percentages -- the reference.
#                    Copied S3-to-S3 (server side, nothing lands here), because an item can be
#                    rewritten in place: S2B_11SKA_20241104_0's item was last written in 2026-08,
#                    its bands in 2024-11.
#   B04/B08/SCL.tif  31.9 GB, read in place from sentinel-cogs by each task, hashed as they arrive
#                    and held to pins.sha256. They were pinned by 00-pin.task.json, on AWS.
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="s3://$BUCKET/inputs/earth-observation-real"
SRC="s3://sentinel-cogs/sentinel-s2-l2a-cogs"

cd "$HERE"
have=$(aws s3 ls --region "$REGION" "$P/items/" | grep -c '_L2A\.json$' || true)
if [ "$have" -lt 153 ]; then
  while read -r scene; do
    id=${scene##*/}
    aws s3 cp --region "$REGION" --only-show-errors "$SRC/$scene/$id.json" "$P/items/$id.json"
  done < scenes.txt
fi
echo "items: $(aws s3 ls --region "$REGION" "$P/items/" | grep -c '_L2A\.json$') staged"

for f in scenes.txt sizes.tsv pin.py real.sh real.py; do
  if [ -f pins.sha256 ] && grep -qE "  $f\$" pins.sha256; then
    grep -E "  $f\$" pins.sha256 | shasum -a 256 -c
  fi
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$P/$f"
done
[ -f pins.sha256 ] && aws s3 cp --region "$REGION" --only-show-errors pins.sha256 "$P/pins.sha256"
echo "staged to $P/"
