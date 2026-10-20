#!/usr/bin/env bash
# Stage rebound-real: JPL's DE440 planetary ephemeris (de440.bsp) and its GM constants
# (gm_de440.tpc) into your bucket, plus this directory's task scripts.
#
# Tier: a versioned public release. DE440 is a frozen JPL product (Park et al. 2021, AJ 161:105,
# doi:10.3847/1538-3881/abd414), served from NAIF's generic-kernel archive. 00-fetch.task.json
# downloads both files ON AWS (nothing passes through this machine) and records their sha256;
# pins.sha256 is the pin: a staged file that differs aborts here and again in-task.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/rebound-real"

aws s3 ls --region "$REGION" "$DST/de440/data.sha256" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" rebound arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/de440/data.sha256" -)"
[ "$got" = "$(cat "$HERE/pins.sha256")" ] || { printf 'FAIL: staged DE440 files are\n%s\npinned\n%s\n' "$got" "$(cat "$HERE/pins.sha256")" >&2; exit 1; }
for f in run.sh ic.py real.py long.py longcheck.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
