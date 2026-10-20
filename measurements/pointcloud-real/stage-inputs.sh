#!/usr/bin/env bash
# Stage pointcloud-real: the pin of USGS 3DEP project CA_LassenNP_2020 on RODA, plus the task's scripts.
#
# Tier: RODA (AWS Open Data, s3://usgs-lidar-public, us-west-2). The point cloud is never copied:
# the real task reads it in place over HTTPS. What is staged is its pin, pins.tsv -- key, size and
# sha256 of all 109,345 objects of the project -- built ON AWS by 00-pin.task.json (pin.py), which
# fetches every object and also checks each single-part object's MD5 against its ETag. The sha256
# of pins.tsv is committed in pins.sha256; the real task re-hashes every object it decodes against it.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/pointcloud-real"

( cd "$HERE" && grep -E '  (pin\.py|real\.py|real\.sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in pin.py real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
if ! aws s3 ls --region "$REGION" "$DST/pins.tsv.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" pointcloud arm64 "$BUCKET" "$(date +%s)" "$HERE/00-pin.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/pins.tsv.sha256" - | awk '{print $1}')"
want="$(awk '$2 == "pins.tsv" {print $1}' "$HERE/pins.sha256")"
[ -n "$want" ] || { echo "pins.sha256 has no pins.tsv line yet; the pin just built is $got" >&2; exit 1; }
[ "$got" = "$want" ] || { echo "FAIL: the staged pins.tsv ($got) is not the committed pin ($want)" >&2; exit 1; }
echo "staged to $DST/ (pins.tsv $want)"
