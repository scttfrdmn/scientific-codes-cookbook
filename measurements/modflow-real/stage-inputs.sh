#!/usr/bin/env bash
# Stage modflow-real: the USGS Mobile Bay MODFLOW 6 model archive (Richins, Duncan and Befus, 2026,
# doi:10.5066/P1XKGCSZ; report SIR 2026-5125, doi:10.3133/sir20265125), plus the task's scripts.
#
# Tier: a durable public id (a USGS data release DOI on ScienceBase item 674f7ffdd34e5a48687db29b).
# The archive is fetched ON AWS by 00-fetch.task.json -- nothing passes through this machine -- and
# kept only if every file matches the size and MD5 ScienceBase publishes for it and every zip entry
# passes its CRC. The fetch writes their sha256, which must equal pins.sha256 here, and the real task
# checks pins.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/modflow-real"

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
if ! aws s3 ls --region "$REGION" "$DST/archive/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" modflow arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
# the run reads the two zips; the readme and metadata are fetched and MD5-checked but not staged in
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/archive/data.sha256" - | grep -E ' archive/(model|output)\.zip$' | sort)"
want="$(grep ' archive/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { echo "FAIL: the staged archive does not match pins.sha256" >&2; exit 1; }
echo "data: $(echo "$want" | grep -c .) archive files match pins.sha256"
( cd "$HERE" && grep -E ' (real\.py|real\.sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
( cd "$HERE" && shasum -a 256 compare.py | diff - compare.sha256 >/dev/null ) || { echo "FAIL: compare.py does not match compare.sha256" >&2; exit 1; }
for f in real.sh real.py pins.sha256 compare.py compare.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
