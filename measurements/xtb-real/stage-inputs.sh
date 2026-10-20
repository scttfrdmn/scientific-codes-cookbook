#!/usr/bin/env bash
# Stage measurements/xtb-real. GMTKN55 is fetched on AWS by 00-fetch.task.json (grimme-lab/GMTKN55 at
# commit 8d485b37, the v1 branch that preserves the 2017 publication; sha256 recorded beside it in the
# bucket). This script checks that pin and stages the scripts.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/xtb-real"
WANT=ee134a03a7c146931f10988965dad52a4d6bdde1d9f1fed5281934b0d7b7dfd1
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/gmtkn55/gmtkn55-v1.sha256" -)" \
  || { echo "no $DST/gmtkn55/gmtkn55-v1.sha256 -- run 00-fetch.task.json first" >&2; exit 1; }
echo "$got" | grep -q "^$WANT  gmtkn55-v1.tar.gz$" \
  || { echo "FAIL: the fetched GMTKN55 archive is not the pinned one" >&2; exit 1; }
for f in run.sh xtb_run.py; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged $DST/"
