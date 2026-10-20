#!/usr/bin/env bash
# Stage the viennarna-real inputs: Rfam 15.1's Rfam.seed.gz -- every curated seed alignment of
# all 4,227 families, 109,390 sequences, each with its family's consensus structure -- plus the
# two task scripts.
#
# Tier: a versioned immutable release (EBI's /pub/databases/Rfam/15.1/, CC0). Streamed from EBI
# straight into the bucket, never written to local disk. Rfam publishes no checksum file for the
# release, so the pin is our sha256 of the bytes, checked here and again on the box.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/viennarna-real"
URL=https://ftp.ebi.ac.uk/pub/databases/Rfam/15.1/Rfam.seed.gz

if ! aws s3 ls --region "$REGION" "$PREFIX/Rfam.seed.gz" >/dev/null 2>&1; then
  curl -sSf "$URL" | aws s3 cp --region "$REGION" --only-show-errors - "$PREFIX/Rfam.seed.gz"
fi
want=$(awk '$2=="Rfam.seed.gz"{print $1}' "$HERE/pins.sha256")
got=$(aws s3 cp --region "$REGION" --only-show-errors "$PREFIX/Rfam.seed.gz" - | shasum -a 256 | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "FAIL: Rfam.seed.gz sha256 $got, pinned $want" >&2; exit 1; }
echo "Rfam.seed.gz sha256 $got == pin"

(cd "$HERE" && grep -E ' (real\.sh|real\.py)$' pins.sha256 | shasum -a 256 -c)
for f in real.sh real.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$PREFIX/$f"
done
echo "staged to $PREFIX/"
