#!/usr/bin/env bash
# Stage sunpy-real: every AIA 171 A image of 2017-09-06, the day of the X9.3 flare (SOL2017-09-06),
# from NASA HelioCloud's RODA bucket s3://gov-nasa-hdrl-data1 (us-east-1, public). 343 files, 5.7 GB,
# product `aia.lev2_hc` (JSOC's level 1 registered and rescaled to 0.5"/px, 5000 x 5000,
# RICE-compressed FITS).
#
# Pinned by inputs.tsv: each object's ETag AND its sha256. The source bucket does not expose its
# versioning, so the sha256 is the pin. The copy is server-side (CopyObject, no bytes through this
# machine) and S3 computes the full-object SHA256 of what it wrote; a mismatch against the pin aborts.
# The task re-checks every staged byte against pins.sha256 before running.
#
#   bash stage-inputs.sh <bucket>          verify-copy against inputs.tsv
#   bash stage-inputs.sh <bucket> --pin    first time only: list the source and write inputs.tsv
set -euo pipefail

BUCKET="${1:?usage: stage-inputs.sh <bucket> [--pin]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_B=gov-nasa-hdrl-data1
SRC_P=sdac/sdo/aia/20170906/0171/
PREFIX="inputs/sunpy-real"

copy() {   # key -> "etag sha256hex"
  local out etag b64
  out=$(aws s3api copy-object --region us-west-2 --bucket "$BUCKET" --key "$PREFIX/aia/${1##*/}" \
          --copy-source "$SRC_B/$1" --checksum-algorithm SHA256 \
          --query '[CopyObjectResult.ETag,CopyObjectResult.ChecksumSHA256]' --output text)
  etag=$(echo "$out" | cut -f1 | tr -d '"')
  b64=$(echo "$out" | cut -f2)
  printf '%s %s\n' "$etag" "$(echo "$b64" | base64 -d | xxd -p -c 64)"
}

if [ "${2:-}" = "--pin" ]; then
  aws s3api list-objects-v2 --region us-east-1 --bucket "$SRC_B" --prefix "$SRC_P" --no-sign-request \
    --query 'Contents[].Key' --output text | tr '\t' '\n' | grep '\.fits$' | sort > "$HERE/.keys"
  : > "$HERE/inputs.tsv"
  while read -r key; do
    read -r etag sha < <(copy "$key")
    printf '%s\t%s\t%s\t%s\n' "${key##*/}" "$key" "$etag" "$sha" >> "$HERE/inputs.tsv"
  done < "$HERE/.keys"
  rm -f "$HERE/.keys"
else
  while IFS=$'\t' read -r name key etag sha; do
    read -r got_etag got < <(copy "$key")
    if [ "$got" != "$sha" ] || [ "$got_etag" != "$etag" ]; then
      echo "FAIL: $name sha256 $got etag $got_etag, pinned $sha $etag" >&2; exit 1
    fi
  done < "$HERE/inputs.tsv"
fi
awk -F'\t' '{print $4"  "$1}' "$HERE/inputs.tsv" > "$HERE/pins.sha256"
for f in real.py run.sh pins.sha256; do
  if [ -f "$HERE/$f" ]; then aws s3 cp --region us-west-2 --only-show-errors "$HERE/$f" "s3://$BUCKET/$PREFIX/$f"; fi
done
echo "staged $(wc -l < "$HERE/inputs.tsv") objects + scripts to s3://$BUCKET/$PREFIX/"
