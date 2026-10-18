#!/usr/bin/env bash
# Stage one network-day: every CI broadband (HH?) channel for 2019-07-06 (day 187), the day of
# the Ridgecrest Mw 7.1, with the StationXML for those stations and the mainshock's analyst
# picks, plus the workload script.
#
# Source: the Southern California Earthquake Data Center on the AWS Open Data Registry,
# s3://scedc-pds, us-west-2, Payer: BucketOwner (checked with
# `aws s3api get-bucket-request-payment`), so every copy below is a same-region server-side copy
# and nothing passes through this machine. 720 MiniSEED day files (9.73 GB), 239 StationXML
# files (1.23 GB), one .phase file. Every object is pinned by sha256 in day187.sha256 and checked
# in-task; the StationXML files are living documents upstream (re-issued when metadata changes),
# so a re-stage that fails the in-task check means upstream moved, not that the run is wrong.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/obspy-real"
SRC="s3://scedc-pds"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

echo "== 720 HH? day files =="
aws s3 cp --recursive --region "$REGION" --only-show-errors \
  "$SRC/continuous_waveforms/2019/2019_187/" "$PREFIX/day187/mseed/" \
  --exclude '*' --include 'CI?????HH????2019187.ms'

echo "== StationXML for the stations that recorded them =="
aws s3 ls --region "$REGION" "$PREFIX/day187/mseed/" | awk '{print substr($4,3,5)}' | tr -d _ | sort -u > "$tmp/sta"
wc -l < "$tmp/sta"
while read -r s; do
  aws s3 cp --region "$REGION" --only-show-errors "$SRC/FDSNstationXML/CI/CI_$s.xml" "$PREFIX/day187/stationxml/CI_$s.xml"
done < "$tmp/sta"

echo "== the mainshock's analyst picks (SCSN event 38457511) =="
aws s3 cp --region "$REGION" --only-show-errors \
  "$SRC/event_phases/2019/2019_187/38457511.phase" "$PREFIX/day187/38457511.phase"

if [ -f "$SCRIPT_DIR/day187.sha256" ]; then
  cp "$SCRIPT_DIR/day187.sha256" "$tmp/pins.sha256"
  ( cd "$SCRIPT_DIR" && shasum -a 256 real.py corename.py ) >> "$tmp/pins.sha256"
  aws s3 cp --region "$REGION" --only-show-errors "$tmp/pins.sha256" "$PREFIX/pins.sha256"
fi
for f in real.py corename.py; do
  aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/$f" "$PREFIX/$f"
done
echo "staged to $PREFIX/"
