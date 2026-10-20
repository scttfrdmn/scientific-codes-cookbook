#!/usr/bin/env bash
# Stage photutils-real: the 100 r-band deep stacks of Pan-STARRS1 3pi projection cell 1749
# (rings.v3.skycell.1749.000-099; image, variance and mask, 6274 x 6258 px each, 12.1 GB) from
# STScI's RODA bucket s3://stpubdata (Payer: BucketOwner, us-east-1).
#
# The matching IPP catalogues (.cmf, 2.9 GB) are already staged by measurements/astropy-real and
# are reused from inputs/astropy-real/cmf/, pinned there by the same mechanism.
#
# Pinned by inputs.tsv: each object's S3 versionId in stpubdata (the bucket is versioned) AND its
# sha256. The copy is server-side (CopyObject, no bytes through this machine) and S3 computes the
# full-object SHA256 of what it wrote; a mismatch against the pin aborts. The task re-checks every
# staged byte against pins.sha256 before running.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail

BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="inputs/photutils-real"

while IFS=$'\t' read -r name key version sha; do
  got=$(aws s3api copy-object --region us-west-2 --bucket "$BUCKET" --key "$PREFIX/ps1/$name" \
          --copy-source "stpubdata/$key?versionId=$version" --checksum-algorithm SHA256 \
          --query CopyObjectResult.ChecksumSHA256 --output text | base64 -d | xxd -p -c 64)
  [ "$got" = "$sha" ] || { echo "FAIL: $name sha256 $got, pinned $sha" >&2; exit 1; }
  echo "$sha  ps1/$name"
done < "$HERE/inputs.tsv"
for f in run.sh real.py pins.sha256 compare.py; do
  aws s3 cp --region us-west-2 --only-show-errors "$HERE/$f" "s3://$BUCKET/$PREFIX/$f"
done
echo "staged $(wc -l < "$HERE/inputs.tsv") objects + scripts to s3://$BUCKET/$PREFIX/"
