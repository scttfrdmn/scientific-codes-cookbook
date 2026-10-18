#!/usr/bin/env bash
# Stage the CDO + NCO real workload: the year of ERA5 that measurements/climate-real staged, xarray's
# results on those bytes, and this directory's scripts -- all pinned in one pins.sha256.
#
# The year itself is not copied again: it is climate-real's 12 objects under
# inputs/climate-real/era5-2023/ (stage them with measurements/climate-real/stage-inputs.sh first),
# pinned by the same sha256s and re-checked in the task. xarray's two result arrays are copied
# server-side from climate-real's arm64 run; its x86 run wrote byte-identical files (same ETag,
# checked here), so they are one reference for all three chips.
set -euo pipefail

B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
PREFIX="s3://$B/inputs/cdo-nco-real"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

aws s3 ls --region "$REGION" "s3://$B/inputs/climate-real/era5-2023/" | wc -l | grep -qx ' *12' \
  || { echo "FAIL: stage measurements/climate-real first (12 ERA5 objects)"; exit 1; }

for f in global_hourly_2023.npy annual_mean_2023.npy; do
  a="$(aws s3api head-object --region "$REGION" --bucket "$B" --key "runs/climate-real/r1/$f" --query ETag --output text)"
  x="$(aws s3api head-object --region "$REGION" --bucket "$B" --key "runs-x86_64/climate-real/r1/$f" --query ETag --output text)"
  [ "$a" = "$x" ] || { echo "FAIL: xarray's $f differs between its arm64 and x86 runs ($a vs $x)"; exit 1; }
  aws s3 cp --region "$REGION" --only-show-errors "s3://$B/runs/climate-real/r1/$f" "$PREFIX/xr_$f"
  echo "$(aws s3 cp --region "$REGION" --only-show-errors "$PREFIX/xr_$f" - | shasum -a 256 | cut -d' ' -f1)  xr_$f" >> "$tmp/pins.sha256"
done

cat "$D/../climate-real/era5-2023.sha256" >> "$tmp/pins.sha256"
( cd "$D" && shasum -a 256 real.sh check.py rt.py ) >> "$tmp/pins.sha256"
for f in real.sh check.py rt.py; do aws s3 cp --region "$REGION" --only-show-errors "$D/$f" "$PREFIX/$f"; done
aws s3 cp --region "$REGION" --only-show-errors "$tmp/pins.sha256" "$PREFIX/pins.sha256"
cp "$tmp/pins.sha256" "$D/pins.sha256"
cat "$tmp/pins.sha256"
echo "staged to $PREFIX/"
