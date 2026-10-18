#!/usr/bin/env bash
# Stage the whole state of Colorado from Copernicus GLO-30 (RODA, s3://copernicus-dem-30m) into the
# cookbook bucket, server-side, and pin every tile by sha256.
#
#   bash measurements/geospatial-real/stage-inputs.sh <bucket> [--bootstrap]
#
# 28 one-degree COGs, N37-N40 x W103-W109 (1.05 GB): Colorado is a lat/lon rectangle, so the
# mosaic has no ocean holes. Each tile is copied with `s3api copy-object --checksum-algorithm
# SHA256`, a single CopyObject (every tile is < 5 GB), so S3 computes the FULL-object sha256 itself
# and nothing passes through this machine. The digest is checked against tiles.sha256 (committed);
# --bootstrap writes that file instead, once. The task re-checks every tile with sha256sum on the box.
set -euo pipefail
BUCKET="${1:?pass your bucket}"
MODE="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC=copernicus-dem-30m
DST="inputs/geospatial-real"
PINS="$HERE/tiles.sha256"
got="$(mktemp)"; trap 'rm -f "$got"' EXIT

for la in 37 38 39 40; do
  for lo in 103 104 105 106 107 108 109; do
    n="Copernicus_DSM_COG_10_N${la}_00_W${lo}_00_DEM"
    b64="$(aws s3api copy-object --region us-west-2 --bucket "$BUCKET" --key "$DST/$n.tif" \
            --copy-source "$SRC/$n/$n.tif" --checksum-algorithm SHA256 \
            --query CopyObjectResult.ChecksumSHA256 --output text)"
    hex="$(printf '%s' "$b64" | base64 -d | od -An -tx1 | tr -d ' \n')"
    echo "$hex  $n.tif" >> "$got"
  done
done

if [ "$MODE" = "--bootstrap" ]; then
  cp "$got" "$PINS"; echo "wrote $(wc -l < "$PINS" | tr -d ' ') pins to $PINS"
else
  diff "$PINS" "$got" || { echo "FAIL: a staged tile does not match its pin" >&2; exit 1; }
  echo "28 tiles match tiles.sha256"
fi

aws s3 cp "$PINS" "s3://$BUCKET/$DST/tiles.sha256" --only-show-errors
for f in real.py sumdiag.py; do aws s3 cp "$HERE/$f" "s3://$BUCKET/$DST/$f" --only-show-errors; done
aws s3 ls "s3://$BUCKET/inputs/geospatial/gie.tar" >/dev/null \
  || { echo "gie.tar is not staged -- run 'make stage RECIPE=geospatial' first" >&2; exit 1; }
echo "staged to s3://$BUCKET/$DST/"
