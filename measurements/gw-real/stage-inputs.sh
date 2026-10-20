#!/usr/bin/env bash
# Stage measurements/gw-real: GW150914's public inputs and this directory's scripts.
#
# Tier: a durable public id. The strain is GWOSC's O1 release (H1, L1, the 4096 s files around the
# event at 4 kHz and 16 kHz, versioned archive URLs); the published posterior is the
# GWTC-2.1 PE data release, Zenodo 10.5281/zenodo.6513631 (v2), checked against Zenodo's md5.
# 00-fetch.task.json downloads them ON AWS and writes the sha256 manifest that inputs.sha256 pins
# and every run re-checks.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/gw-real"
for f in fetch.py inspect_release.py gwreal.py search.py pe.py run.sh inputs.sha256; do
  [ -f "$HERE/$f" ] && aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
aws s3 ls --region "$REGION" "$DST/data/inputs.sha256" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" gw arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/data/inputs.sha256" -)"
[ "$got" = "$(cat "$HERE/inputs.sha256")" ] || { echo "FAIL: staged inputs do not match inputs.sha256" >&2; exit 1; }
echo "staged $DST/"
