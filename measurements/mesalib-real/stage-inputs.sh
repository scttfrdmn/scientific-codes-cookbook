#!/usr/bin/env bash
# Stage the mesalib-real input and scripts.
#
#   bash measurements/mesalib-real/stage-inputs.sh <bucket>
#
# Data: four 1-arc-second SRTM tiles of the Western Alps (N45-46, E006-007: Mont Blanc, the
# Matterhorn, Lake Geneva, the Aosta valley), as Mapzen/Tilezen publish them on the AWS Open Data
# Registry ("Terrain Tiles", s3://elevation-tiles-prod/skadi/, Payer: BucketOwner). Tier: RODA. Each
# tile is a gzipped .hgt, 3601 x 3601 big-endian int16 metres; together they are a 7201 x 7201
# mosaic, 103.7 million triangles. The copy is server-side (CopyObject, no bytes through this
# machine) and S3 computes the full-object SHA256 of what it wrote; a mismatch against pins.sha256
# aborts. The task re-checks every staged byte against the same pins.
set -euo pipefail

BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="inputs/mesalib-real"

for t in N45/N45E006 N45/N45E007 N46/N46E006 N46/N46E007; do
  name="${t#*/}.hgt.gz"
  want=$(awk -v f="skadi/$name" '$2==f{print $1}' "$HERE/pins.sha256")
  got=$(aws s3api copy-object --region "$REGION" --bucket "$BUCKET" --key "$PREFIX/skadi/$name" \
          --copy-source "elevation-tiles-prod/skadi/$t.hgt.gz" --checksum-algorithm SHA256 \
          --query CopyObjectResult.ChecksumSHA256 --output text | base64 -d | xxd -p -c 64)
  [ "$got" = "$want" ] || { echo "FAIL: $name sha256 $got, pinned ${want:-<none>}"; exit 1; }
  echo "ok  $got  $name"
done

cd "$HERE"
grep -E ' (real\.sh|terrain\.py)$' pins.sha256 | shasum -a 256 -c
for f in real.sh terrain.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "s3://$BUCKET/$PREFIX/$f"
done
echo "staged to s3://$BUCKET/$PREFIX/"
