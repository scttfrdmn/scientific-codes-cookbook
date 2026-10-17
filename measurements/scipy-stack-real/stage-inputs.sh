#!/usr/bin/env bash
# Stage one complete year of GHCN-Daily (RODA: s3://noaa-ghcn-pds) and the workload script.
#
# NOAA rewrites every by_year file daily as late reports arrive, so the RODA object is not
# immutable. The pin is the sha256 of the bytes staged here, from GHCN-Daily
# 3.35-upd-2026101517; a re-stage after NOAA's next update fails this check by design.
# To run on a newer snapshot, re-pin and re-derive (nothing below asserts a GHCN-specific value).
set -euo pipefail
BUCKET="s3://${1:?usage: stage-inputs.sh <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

cat > data.sha256 <<'PINS'
62a950b9706b07b98af7c1361208f0283f671e170505b8fff1e0ab271860c03d  2024.csv
428eabc955883187935bce283766d5646e3ed7484b92eb0803874c0e8dd9b342  ghcnd-stations.txt
PINS
for f in 2024.csv ghcnd-stations.txt; do
  src="s3://noaa-ghcn-pds/$f"; [ "$f" = 2024.csv ] && src="s3://noaa-ghcn-pds/csv/by_year/$f"
  aws s3 cp --no-sign-request --only-show-errors "$src" "$f"
done
aws s3 cp --no-sign-request --only-show-errors s3://noaa-ghcn-pds/ghcnd-version.txt - | head -1
shasum -a 256 -c data.sha256 || { echo "NOAA has updated the snapshot since it was pinned -- re-pin" >&2; exit 1; }

cp "$HERE/ghcn_workload.py" .
shasum -a 256 ghcn_workload.py > pins.sha256
for f in 2024.csv ghcnd-stations.txt data.sha256 ghcn_workload.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$BUCKET/inputs/scipy-stack-real/$f"
done
echo "staged to $BUCKET/inputs/scipy-stack-real/"
