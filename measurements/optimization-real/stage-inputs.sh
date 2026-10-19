#!/usr/bin/env bash
# Stage optimization-real: MIPLIB 2017's benchmark set (240 instances, its test list and solution
# file v37), Netlib LP (COIN-OR Data-Netlib at tag releases/1.2.11), SoPlex's committed Netlib
# optima (tag v8.1.0) and netlib's readme, plus the scripts the task runs.
#
# Tier: a durable public id. MIPLIB's list and solution file are versioned; the two git sources
# are immutable tags. benchmark.zip and netlib's readme have no version in their URL, so they are
# pinned by sha256 on first fetch and checked at every stage-in. The data is fetched ON AWS by
# 00-fetch.task.json (nothing passes through this machine), and the fetch's own sha256 of what it
# kept must equal pins.sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/optimization-real"

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
if ! aws s3 ls --region "$REGION" "$DST/data/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" optimization arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/data/data.sha256" - | awk '{print $1"  data/"$2}' | sort)"
want="$(grep ' data/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { printf 'FAIL: staged data differs from pins.sha256:\n%s\n' "$(diff <(echo "$want") <(echo "$got"))" >&2; exit 1; }
echo "data: $(echo "$got" | wc -l | tr -d ' ') files match pins.sha256"
( cd "$HERE" && grep -vE ' data/' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in driver.py solve.py summarize.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
