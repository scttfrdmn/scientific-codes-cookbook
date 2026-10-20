#!/usr/bin/env bash
# Stage datashader-real: every NYC TLC yellow-taxi trip of 2009 and 2010 (24 monthly Parquet files,
# 11.4 GB), plus this measurement's scripts, into your bucket.
#
#   bash measurements/datashader-real/stage-inputs.sh <bucket>
#
# Tier: a stable public source, not RODA. RODA lists the TLC data as s3://nyc-tlc, but that bucket
# refuses signed and anonymous requests alike (403, measurements/r-real). So 00-fetch.task.json
# downloads the files ON AWS from the TLC's own CloudFront origin (nothing passes through this
# machine), checks each byte count against Content-Length and records its sha256. pins.sha256 is
# that record as fetched on 2026-10-20: a re-issued month fails here, and again in-task.
# 2009-2010 are the only years the TLC still publishes with pickup/dropoff coordinates; from 2011
# its Parquet files carry taxi-zone IDs instead.
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/datashader-real"

aws s3 ls --region "$REGION" "$DST/tlc/data.sha256" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" datashader arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/tlc/data.sha256" -)"
[ "$got" = "$(cat "$HERE/pins.sha256")" ] || { printf 'FAIL: staged TLC files are\n%s\npinned\n%s\n' "$got" "$(cat "$HERE/pins.sha256")" >&2; exit 1; }
for f in run.sh real.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
