#!/usr/bin/env bash
# Stage GFDL-CM4 historical daily near-surface air temperature (CMIP6, r1i1p1f1, gr1, v20180701)
# twice -- once as the Pangeo Zarr store on AWS Open Data, once as the ESGF netCDF it was converted
# from -- plus the cell-area store and the scripts. Server-side S3 copies only; nothing is downloaded.
#
#   zarr   s3://cmip6-pds/CMIP6/CMIP/NOAA-GFDL/GFDL-CM4/historical/r1i1p1f1/day/tas/gr1/v20180701/
#          102 objects, 7.78 GB, us-west-2, Payer: BucketOwner. Pinned by tas.md5: every object is a
#          single-part upload, so its S3 ETag is the publisher's own MD5 of the bytes.
#   area   s3://cmip6-pds/.../fx/areacella/gr1/v20180701/  (21 objects, areacella.md5, same rule)
#   netCDF s3://esgf-world/CMIP6/... (us-east-2), 9 files, 7.18 GB. Pinned by esgf-nc.sha256: the
#          SHA256 ESGF publishes for each file (identical at all four replicas the ESGF index lists).
# Both pins are checked here against the source listing and again in-task against the staged bytes.
#
#   bash stage-inputs.sh <bucket>          # re-running only re-stages the scripts and pins
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="$BUCKET/inputs/zarr-dask-real"
REL=CMIP6/CMIP/NOAA-GFDL/GFDL-CM4/historical/r1i1p1f1
TAS="$REL/day/tas/gr1/v20180701"
AREA="$REL/fx/areacella/gr1/v20180701"
cd "$HERE"

# the source listings must still be what we pinned: same keys, same single-part MD5 ETags
for pair in "tas.md5:$TAS:tas.zarr" "areacella.md5:$AREA:areacella.zarr"; do
  pin="${pair%%:*}"; rest="${pair#*:}"; prefix="${rest%%:*}"; name="${rest#*:}"
  aws s3api list-objects-v2 --region us-west-2 --bucket cmip6-pds --prefix "$prefix/" \
    --query 'Contents[].[ETag,Key]' --output text \
    | awk -v p="$prefix/" '{gsub(/"/,"",$1); print $1"  "substr($2,length(p)+1)}' \
    | diff - "$pin" > /dev/null || { echo "FAIL: s3://cmip6-pds/$prefix/ no longer matches $pin" >&2; exit 1; }
  echo "ok  $pin matches the RODA listing ($(wc -l < "$pin" | tr -d ' ') objects)"
  if ! aws s3 ls "$DST/$name/.zmetadata" > /dev/null 2>&1; then
    aws s3 cp --only-show-errors --copy-props none --recursive --region us-west-2 "s3://cmip6-pds/$prefix/" "$DST/$name/"
  fi
done

if [ "$(aws s3 ls "$DST/nc/" | wc -l | tr -d ' ')" != 9 ]; then
  while read -r _ f; do
    aws s3 cp --only-show-errors --copy-props none --source-region us-east-2 --region us-west-2 \
      "s3://esgf-world/$TAS/${f#nc/}" "$DST/$f"
  done < esgf-nc.sha256
fi

shasum -a 256 ncref.py real.py run.sh > pins.sha256
cat pins.sha256
for f in ncref.py real.py run.sh pins.sha256 tas.md5 areacella.md5 esgf-nc.sha256; do
  aws s3 cp --only-show-errors --region us-west-2 "$f" "$DST/$f"
done
aws s3 ls --recursive --summarize "$DST/" | tail -2
echo "staged to $DST/"
