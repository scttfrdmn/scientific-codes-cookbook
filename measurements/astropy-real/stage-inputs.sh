#!/usr/bin/env bash
# Stage the astropy-real field into your bucket: one HEALPix order-4 pixel (808, centre RA 298.1,
# Dec +17.0, l 55, b -5; 13.4 deg^2 in Sagitta/Vulpecula), from STScI's RODA bucket s3://stpubdata
# (Payer: BucketOwner, us-east-1).
#   gaia/  the 4 Gaia DR3 gaia_source HATS partitions (Norder=5, Npix 3232-3235)
#   ps1/   the 4 Pan-STARRS1 DR2 ObjectThin+MeanObject HATS partitions of the same 4 pixels
#   cmf/   all 100 r-band stack catalogues (.cmf) of PS1 projection cell 1749, which overlaps it
#
# Pinned by inputs.tsv: each object's S3 versionId in stpubdata (the bucket is versioned) AND its
# sha256. The copy is server-side (CopyObject, no bytes through this machine) and S3 computes the
# full-object SHA256 of what it wrote; a mismatch against the pin aborts. The task re-checks every
# staged byte against pins.sha256 before running.
set -euo pipefail

BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="inputs/astropy-real"

while IFS=$'\t' read -r name key version sha; do
  got=$(aws s3api copy-object --region us-west-2 --bucket "$BUCKET" --key "$PREFIX/$name" \
          --copy-source "stpubdata/$key?versionId=$version" --checksum-algorithm SHA256 \
          --query CopyObjectResult.ChecksumSHA256 --output text | base64 -d | xxd -p -c 64)
  [ "$got" = "$sha" ] || { echo "FAIL: $name sha256 $got, pinned $sha"; exit 1; }
  echo "$sha  $name"
done < "$HERE/inputs.tsv" > /tmp/astropy-real-pins.sha256

( cd "$HERE" && shasum -a 256 real.py ) >> /tmp/astropy-real-pins.sha256
cmp /tmp/astropy-real-pins.sha256 "$HERE/pins.sha256" || { echo "FAIL: pins.sha256 is stale"; exit 1; }
aws s3 cp --region us-west-2 --only-show-errors "$HERE/real.py" "s3://$BUCKET/$PREFIX/real.py"
aws s3 cp --region us-west-2 --only-show-errors "$HERE/pins.sha256" "s3://$BUCKET/$PREFIX/pins.sha256"
echo "staged $(wc -l < "$HERE/inputs.tsv") objects + real.py to s3://$BUCKET/$PREFIX/"
