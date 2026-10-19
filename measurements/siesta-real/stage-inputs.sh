#!/usr/bin/env bash
# Stage siesta-real: SIESTA 5.4.2's own test suite -- every test input, its committed
# Reference/*.out, the suite's pseudopotentials and its comparison tools -- plus the task's scripts.
#
# Tier: a durable public id, a versioned release. The files are fetched ON AWS by
# 00-fetch.task.json from the siesta-project repository at the 5.4.2 tag's commit (0e2722c) and
# kept only if each equals its git blob in that tree (manifest.tsv). The fetch writes their sha256,
# which must equal pins.sha256 here; the real task checks pins.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/siesta-real"

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
aws s3 cp --region "$REGION" --only-show-errors "$HERE/manifest.tsv" "$DST/manifest.tsv"
if ! aws s3 ls --region "$REGION" "$DST/suite/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" siesta arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/suite/data.sha256" - | sort)"
want="$(grep ' suite/tests/' "$HERE/pins.sha256" | sed 's| suite/tests/| tests/|' | sort)"
[ "$got" = "$want" ] || { echo "FAIL: the staged suite does not match pins.sha256" >&2; exit 1; }
echo "data: $(echo "$want" | grep -c .) files match pins.sha256"
( cd "$HERE" && grep -E ' real\.(py|sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
aws s3 cp --region "$REGION" --only-show-errors "$HERE/real.py" "$DST/real.py"
aws s3 cp --region "$REGION" --only-show-errors "$HERE/real.sh" "$DST/real.sh"
aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.sha256" "$DST/pins.sha256"
echo "staged to $DST/"
