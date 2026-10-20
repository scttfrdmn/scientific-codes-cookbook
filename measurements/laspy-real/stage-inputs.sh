#!/usr/bin/env bash
# Stage laspy-real: the 128 source LAZ tiles of USGS 3DEP project CA_LassenNP_2020, plus the task's
# scripts and the two catalogues the run checks the tiles against.
#
# Tier: RODA (AWS Open Data). The tiles are USGS's delivered LAZ in the requester-pays bucket
# s3://usgs-lidar (us-west-2) -- the very files the CA_LassenNP_2020 EPT on s3://usgs-lidar-public
# was built from (its ept-sources/manifest.json lists these 128 paths). They are copied S3-to-S3,
# server-side and in-region, into your bucket, because a spawn task cannot sign requester-pays
# requests from inside the container. Nothing passes through this machine.
#
# Pins: src.tsv is RODA's listing (key, size, ETag, LastModified), committed. The run checks every
# tile's size and MD5 / multipart ETag against it, and its sha256 against pins.tsv (written by the
# probe run, committed); pins.sha256 pins the scripts, the catalogues and pins.tsv.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/laspy-real"
PROJ="Projects/CA_LassenNP_LowerBearCreekHUC10_2020_D20/CA_LassenNP_2020"

( cd "$HERE" && grep -vE '  (pins\.tsv|manifest\.json|CA_LassenNP_2020\.vpc)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.py real.sh src.tsv pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
[ -f "$HERE/pins.tsv" ] && aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.tsv" "$DST/pins.tsv"

# the EPT's sources manifest (pointcloud-real pinned the same object) and USGS's virtual point cloud
aws s3 cp --region "$REGION" --only-show-errors "s3://usgs-lidar-public/CA_LassenNP_2020/ept-sources/manifest.json" "$DST/manifest.json"
aws s3 cp --region "$REGION" --only-show-errors --request-payer requester "s3://usgs-lidar/$PROJ/CA_LassenNP_2020.vpc" "$DST/CA_LassenNP_2020.vpc"

have=$({ aws s3 ls --region "$REGION" "$DST/laz/" || true; } | awk '{n++; s+=$3} END{print n+0, s+0}')
want=$(awk -F'\t' '{n++; s+=$2} END{print n, s}' "$HERE/src.tsv")
if [ "$have" != "$want" ]; then
  aws s3 cp --region "$REGION" --only-show-errors --recursive --request-payer requester --copy-props none \
    "s3://usgs-lidar/$PROJ/LAZ/" "$DST/laz/"
  have=$({ aws s3 ls --region "$REGION" "$DST/laz/" || true; } | awk '{n++; s+=$3} END{print n+0, s+0}')
fi
[ "$have" = "$want" ] || { echo "FAIL: staged tiles ($have) are not RODA's listing ($want)" >&2; exit 1; }
echo "staged to $DST/ (tiles: $have)"
