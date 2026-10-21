#!/usr/bin/env bash
# Stage tomography-real: the BBii sandstone scan (SLS TOMCAT beamline X02DA, 2013; Bruns, Stipp &
# Sorensen, Adv. Water Res. 105, 96-107, 2017) as its depositors published it -- Zenodo record
# 4912435 v1.0, doi:10.5281/zenodo.4912435, CC BY 4.0 -- plus the task's scripts.
#
# Tier: a durable public id (a Zenodo DOI). proj.zip (17.1 GB, every raw TIFF) and rec.zip
# (25.3 GB, the depositors' own reconstruction of every slice) are fetched ON AWS by
# 00-fetch.task.json -- nothing passes through this machine -- and kept only if size and MD5 equal
# Zenodo's published values. The fetch writes their sha256, which must equal pins.sha256 here, and
# the real task checks pins.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/tomography-real"

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
if ! aws s3 ls --region "$REGION" "$DST/data/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" tomography arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/data/data.sha256" -)"
want="$(grep -E ' (proj|rec)\.zip$' "$HERE/pins.sha256")"
[ "$got" = "$want" ] || { echo "FAIL: the staged zips do not match pins.sha256" >&2; exit 1; }
echo "data: proj.zip and rec.zip match pins.sha256"
( cd "$HERE" && grep -E ' (real\.py|real\.sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.sh real.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
