#!/usr/bin/env bash
# Stage the whole OpenAlex `institutions` entity (release 2026-10-14: 83 gzipped JSON Lines parts,
# 143,455 records, 200,441,980 bytes) plus this measurement's script into the cookbook bucket.
#
#   bash measurements/r-jsonlite-real/stage-inputs.sh <bucket>
#
# DATA TIER: RODA. OpenAlex is on the AWS Open Data registry as s3://openalex (public, us-east-1).
# The copy is server-side, S3 to S3 -- nothing passes through this machine. The snapshot is replaced
# every release (quarterly), so the pin is the sha256 of each part as copied, computed ON AWS by
# hash.task.json and committed as institutions.sha256. A later release fails here rather than
# silently changing the dataset. OpenAlex's own manifest (per-part record_count and content_length)
# is copied alongside: it is the source's own count, which the run asserts against.
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC=s3://openalex/data/jsonl/institutions
DST="s3://$BUCKET/inputs/r-jsonlite-real"

n="$(aws s3 ls --region "$REGION" --recursive "$DST/institutions/" | grep -c 'part_0000\.gz$' || true)"
if [ "$n" != 83 ]; then
  echo "== copying $SRC/ server-side (release 2026-10-14) =="
  aws s3 cp --recursive --only-show-errors --source-region us-east-1 --region "$REGION" \
    "$SRC/" "$DST/institutions/" --exclude manifest.json
  aws s3 cp --only-show-errors --source-region us-east-1 --region "$REGION" \
    "$SRC/manifest.json" "$DST/manifest.json"
fi

if aws s3 ls --region "$REGION" "$DST/institutions.fetched.sha256" >/dev/null 2>&1; then
  # the hash task's digests of the staged bytes must equal the committed pins
  aws s3 cp --region "$REGION" --only-show-errors "$DST/institutions.fetched.sha256" - \
    | diff "$HERE/institutions.sha256" - \
    || { echo "FAIL: the staged parts are not the pinned bytes (a later OpenAlex release?)" >&2; exit 1; }
  echo "83 parts + manifest staged, digests match institutions.sha256"
else
  echo "parts staged but not yet hashed on AWS -- run hash.task.json, then re-run this script:" >&2
  echo "  sed \"s|\\\${COOKBOOK_BUCKET}|$BUCKET|g\" $HERE/hash.task.json > /tmp/h.json && spawn task run --spec /tmp/h.json --wait" >&2
  exit 1
fi

aws s3 cp --region "$REGION" --only-show-errors "$HERE/institutions.sha256" "$DST/institutions.sha256"
aws s3 cp --region "$REGION" --only-show-errors "$HERE/real.R" "$DST/real.R"
echo "real.R  $(shasum -a 256 "$HERE/real.R" | cut -d' ' -f1)"
echo "staged to $DST/"
