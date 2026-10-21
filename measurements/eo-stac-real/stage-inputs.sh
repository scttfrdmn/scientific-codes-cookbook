#!/usr/bin/env bash
# Stage the eo-stac-real scripts and the pinned snapshot. Nothing passes through this machine.
#
#   bash measurements/eo-stac-real/stage-inputs.sh <bucket>
#
# Data (tier: RODA): the Earth Search v1 `sentinel-2-l2a` STAC API (element84, us-west-2) over the
# AWS Open Data `sentinel-cogs` bucket it indexes. Both are live, so the reference is a snapshot:
# 00-pin.task.json runs the search and the bucket listing on AWS and writes
#   snapshot/items.jsonl.gz   every returned item, canonical JSON, sorted by id
#   snapshot/listing.tsv.gz   (key, size, ETag) of every object in those items' scene directories
# which are copied S3-to-S3 to inputs/eo-stac-real/snapshot/ and held to pins.sha256.
# Reused, not copied: earth-observation-real's scenes.txt, sizes.tsv and pins.sha256 (tile 11SKA).
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="s3://$BUCKET/inputs/eo-stac-real"

aws s3 ls --region "$REGION" "s3://$BUCKET/inputs/earth-observation-real/scenes.txt" >/dev/null \
  || { echo "stage earth-observation-real first: bash measurements/earth-observation-real/stage-inputs.sh $BUCKET" >&2; exit 1; }
cd "$HERE"
grep -E '  real\.(py|sh)$' pins.sha256 | shasum -a 256 -c
for f in real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$P/$f"
done
if ! aws s3 ls --region "$REGION" "$P/snapshot/items.jsonl.gz" >/dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" eo-stac arm64 "$BUCKET" "$(date +%s)" "$HERE/00-pin.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait || true   # pin mode stages its snapshot even if a check fails
  aws s3 cp --region "$REGION" --only-show-errors --recursive \
    "s3://$BUCKET/runs/eo-stac-real/pin/snapshot/" "$P/snapshot/"
fi
aws s3 cp --region "$REGION" --only-show-errors "$P/snapshot/snapshot.sha256" -
echo "those must equal the snapshot/ lines of pins.sha256; the check task enforces it"
