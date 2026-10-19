#!/usr/bin/env bash
# Stage healpy-real: Planck PR3 (2018) SMICA CMB IQU map at Nside 2048, the common intensity and
# polarisation masks, and Planck's published TT/EE spectra and best-fit theory, into your bucket.
#
# Tier: a versioned public release. The Planck Legacy Archive's products are fixed by their release
# tag (R3.00 / R3.01 in every name); 00-fetch.task.json downloads them from the PLA's IRSA mirror ON
# AWS (nothing passes through this machine), checks each byte count and records its sha256, and
# pins.sha256 is the pin: a staged file that differs aborts here and again in-task.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/healpy-real"

aws s3 ls --region "$REGION" "$DST/pr3/data.sha256" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" healpy arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/pr3/data.sha256" -)"
[ "$got" = "$(cat "$HERE/pins.sha256")" ] || { printf 'FAIL: staged Planck files are\n%s\npinned\n%s\n' "$got" "$(cat "$HERE/pins.sha256")" >&2; exit 1; }
for f in run.sh real.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
