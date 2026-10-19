#!/usr/bin/env bash
# Stage su2-real: SU2 8.5.0's own NACA 0012 inputs -- NASA TMR's 897x257 grid, the 225x65 grid and
# the committed converged solution its regression restarts from, and upstream's SA config -- plus
# this directory's scripts.
#
# Tier: a durable public id, a versioned release. The files are fetched ON AWS by
# 00-fetch.task.json from raw.githubusercontent.com at the v8.5.0 commits of su2code/TestCases and
# su2code/SU2, and kept only if each equals its git blob in that tree (manifest.tsv). The fetch
# writes their sha256, which must equal pins.sha256; the real task checks pins.sha256 at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/su2-real"

for f in fetch.py manifest.tsv; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
if ! aws s3 ls --region "$REGION" "$DST/data/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" su2 arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/data/data.sha256" - | sort)"
want="$(grep ' data/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { echo "FAIL: the staged SU2 inputs do not match pins.sha256" >&2; exit 1; }
echo "data: $(echo "$want" | grep -c .) files match pins.sha256"
( cd "$HERE" && grep -E ' (real\.py|run\.sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.py run.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
