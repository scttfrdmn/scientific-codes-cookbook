#!/usr/bin/env bash
# Stage mne-real: subject 01's whole MEG session from OpenNeuro ds000117 v2.0.0 (Wakeman & Henson
# 2015, doi:10.18112/openneuro.ds000117.v2.0.0) -- six runs of 306-channel Elekta MEG + 70 EEG at
# 1100 Hz (5.24 GB), their BIDS events and channels, and the site's fine-calibration and cross-talk
# files -- plus the scripts.
#
# Tier: RODA. OpenNeuro's bucket on AWS Open Data (s3://openneuro.org, us-east-1), copied server-side
# into the bucket. The source path is "latest", so the pin is not the path: sources.tsv records each
# object's S3 VersionId and single-part ETag (its md5) as listed on 2026-10-18; 00-pin.task.json
# checks the staged copies against those md5s ON AWS and writes their sha256 (data.sha256), which
# every run checks again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/mne-real"
SRC=s3://openneuro.org/ds000117/sub-01/ses-meg/meg

have=$( { aws s3 ls "$DST/meg/" --region "$REGION" || true; } | awk '{print $4}' | sort)
tail -n +2 "$HERE/sources.tsv" | while IFS=$'\t' read -r f _vid _md5 _bytes; do
  grep -qxF "$f" <<<"$have" && continue
  aws s3 cp "$SRC/$f" "$DST/meg/$f" --source-region us-east-1 --region "$REGION" --copy-props none --only-show-errors
  echo "  copied $f"
done
aws s3 cp --region "$REGION" --only-show-errors "$HERE/sources.tsv" "$DST/sources.tsv"
if [ ! -s "$HERE/data.sha256" ]; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" mne arm64 "$BUCKET" "$(date +%s)" "$HERE/00-pin.task.json")"
  aws s3 cp --region "$REGION" --only-show-errors "s3://$BUCKET/runs/mne-real/pin/data.sha256" "$HERE/data.sha256"
fi
# pins.sha256 = the data pins (from AWS) + the two scripts, so a run checks everything it reads
( cd "$HERE" && cat data.sha256 && { sha256sum real.py real.sh 2>/dev/null || shasum -a 256 real.py real.sh; } ) > "$HERE/pins.sha256"
for f in real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/ ($(wc -l < "$HERE/data.sha256" | tr -d ' ') data files pinned)"
