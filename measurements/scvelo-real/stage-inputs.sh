#!/usr/bin/env bash
# Stage scvelo-real: scVelo's mouse gastrulation atlas (Pijuan-Sala et al., Nature 2019,
# doi:10.1038/s41586-019-0933-9) as scVelo itself distributes it -- figshare article 14627496 v1,
# doi:10.6084/m9.figshare.14627496.v1, file 28095525, CC BY 4.0 -- plus the task's scripts.
#
# Tier: a durable public id (a figshare DOI). The 4.69 GB file is fetched ON AWS by
# 00-fetch.task.json -- nothing passes through this machine -- and kept only if its size and MD5
# equal figshare's published values. The fetch writes its sha256, which must equal pins.sha256
# here, and the real task checks pins.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/scvelo-real"

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
if ! aws s3 ls --region "$REGION" "$DST/data/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" scvelo arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/data/data.sha256" -)"
want="$(grep ' gastrulation.h5ad$' "$HERE/pins.sha256")"
[ "$got" = "$want" ] || { echo "FAIL: the staged atlas does not match pins.sha256" >&2; exit 1; }
echo "data: gastrulation.h5ad matches pins.sha256"
( cd "$HERE" && grep -E ' (real\.py|real\.sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.sh real.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
