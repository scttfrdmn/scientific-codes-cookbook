#!/usr/bin/env bash
# Stage one full year (2023) of hourly ERA5 2 m temperature, 0.25 deg, and the workload script.
#
# Source: NSF NCAR's ERA5 on the AWS Open Data Registry (RDA ds633.0), s3://nsf-ncar-era5,
# us-west-2, Payer: BucketOwner (checked with `aws s3api get-bucket-request-payment`), so the
# reads cost the requester nothing and the copy below is a same-region server-side copy.
# The 12 monthly objects are pinned by sha256 in era5-2023.sha256, which this script checks by
# streaming each object from the source (no local disk) before copying it. 13.35 GB in all.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/climate-real"
SRC="s3://nsf-ncar-era5/e5.oper.an.sfc"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

while read -r sha name; do
  month="${name#era5/e5.oper.an.sfc.128_167_2t.ll025sc.}"; month="${month:0:6}"
  key="$SRC/$month/${name#era5/}"
  got="$(aws s3 cp --no-sign-request --region "$REGION" --only-show-errors "$key" - | shasum -a 256 | cut -d' ' -f1)"
  [ "$got" = "$sha" ] || { echo "FAIL: $key is $got, pinned $sha"; exit 1; }
  aws s3 cp --region "$REGION" --only-show-errors "$key" "$PREFIX/era5-2023/${name#era5/}"
  echo "ok  $month"
done < "$SCRIPT_DIR/era5-2023.sha256"

cp "$SCRIPT_DIR/era5-2023.sha256" "$tmp/pins.sha256"
( cd "$SCRIPT_DIR" && shasum -a 256 era5.py ) >> "$tmp/pins.sha256"
# The task stages code/ as one directory (EC2 user data is 16 KiB, spawn#1246); the spec pins
# era5-2023.sh by sha256, and that script checks era5.py against pins.sha256.
for f in era5.py era5-2023.sh; do
  aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/$f" "$PREFIX/code/$f"
done
aws s3 cp --region "$REGION" --only-show-errors "$tmp/pins.sha256" "$PREFIX/code/pins.sha256"
cat "$tmp/pins.sha256"
echo "staged to $PREFIX/"
