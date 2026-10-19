#!/usr/bin/env bash
# Stage the paraview-real input and scripts.
#
#   bash measurements/paraview-real/stage-inputs.sh <bucket>
#
# Data: the first week of 2023 (1-7 January, 168 hours) of ERA5 temperature on all 37 pressure
# levels at 0.25 deg (1440 x 721 x 37 per hour), as NSF NCAR publishes it on the AWS Open Data
# Registry (RDA ds633.0, s3://nsf-ncar-era5, us-west-2, Payer: BucketOwner). Tier: RODA. Seven
# daily netCDF4 files, 8.86 GB. The copy is server-side (CopyObject, no bytes through this
# machine) and S3 computes the full-object SHA256 of what it wrote; a mismatch against
# pins.sha256 aborts. The task re-checks every staged byte against the same pins.
set -euo pipefail

BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="inputs/paraview-real"

for d in 01 02 03 04 05 06 07; do
  name="e5.oper.an.pl.128_130_t.ll025sc.202301${d}00_202301${d}23.nc"
  want=$(awk -v f="era5/$name" '$2==f{print $1}' "$HERE/pins.sha256")
  got=$(aws s3api copy-object --region "$REGION" --bucket "$BUCKET" --key "$PREFIX/era5-t/$name" \
          --copy-source "nsf-ncar-era5/e5.oper.an.pl/202301/$name" --checksum-algorithm SHA256 \
          --query CopyObjectResult.ChecksumSHA256 --output text | base64 -d | xxd -p -c 64)
  [ "$got" = "$want" ] || { echo "FAIL: $name sha256 $got, pinned $want"; exit 1; }
  echo "ok  $got  $name"
done

cd "$HERE"
grep -E ' (real\.sh|week\.py)$' pins.sha256 | shasum -a 256 -c
for f in real.sh week.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "s3://$BUCKET/$PREFIX/$f"
done
echo "staged to s3://$BUCKET/$PREFIX/"
