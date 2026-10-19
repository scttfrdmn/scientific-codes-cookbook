#!/usr/bin/env bash
# Stage cmdstan-real: posteriordb 1.1.0's reference set -- the 57 posteriors that ship reference
# draws, with their models, data and reference info (256 files, 32 MB) -- plus the task's scripts.
#
# Tier: a durable public id, a versioned release. The files are fetched ON AWS by
# 00-fetch.task.json from raw.githubusercontent.com at the release commit (299b77c, tag 1.1.0) and
# kept only if each equals its git blob in the release tree (manifest.tsv). The fetch writes their
# sha256, which must equal pins.sha256 here; the real task checks pins.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/cmdstan-real"

for f in fetch.py manifest.tsv; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
if ! aws s3 ls --region "$REGION" "$DST/pdb/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" cmdstan arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/pdb/data.sha256" - | sort)"
want="$(grep ' pdb/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { echo "FAIL: the staged posteriordb files do not match pins.sha256" >&2; exit 1; }
echo "data: $(echo "$want" | grep -c .) files match pins.sha256"
( cd "$HERE" && grep -E ' real\.(py|sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
