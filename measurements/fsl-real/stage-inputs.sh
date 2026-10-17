#!/usr/bin/env bash
# Stage measurements/fsl-real: the NFBS cohort, FNIRT + applywarp from FSL's channel, and the scripts.
#
# THE DATA (RODA tier). The NeuroFeedback Skull-stripped repository (NFBS; Puccio et al. 2016,
# GigaScience, doi:10.1186/s13742-016-0150-5): 125 T1w MPRAGE scans from the Enhanced Rockland
# Sample, each with a brain mask that was BEaST-generated and then *manually corrected*. It is on
# AWS Open Data in INDI's bucket as one archive written 2016-10-17 and never replaced; the pin is
# its sha256, computed from the source object and asserted again on the box before any compute.
#
# THE TOOLS. recipes/fsl must be staged first (`make stage RECIPE=fsl`): its 89 packages per
# architecture are reused as-is. This adds the two FSL-channel packages the recipe does not carry
# -- fsl-fnirt (fnirt and its T1_2_MNI152_2mm config) and fsl-fugue (applywarp) -- verified against
# fsl-real-linux-*.lock, which is the recipe lock plus those two lines.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DIR="$(cd "$(dirname "$0")" && pwd)"
DST="$BUCKET/inputs/fsl-real"

SRC=s3://fcp-indi/data/Projects/RocklandSample/NFBS_Dataset.tar.gz
SIZE=1751464473
SHA=fd616b9ea21aad3d0d4cd05a25a71f4a3c251140df38188e58e499254440d2cc

aws s3 ls "$BUCKET/inputs/fsl/pkgs/linux-aarch64/" --region "$REGION" >/dev/null \
  || { echo "stage recipes/fsl first: make stage RECIPE=fsl" >&2; exit 1; }

if aws s3 ls "$DST/NFBS_Dataset.tar.gz" --region "$REGION" >/dev/null 2>&1; then
  echo "  NFBS_Dataset.tar.gz already cached"
else
  echo "  copying NFBS_Dataset.tar.gz from RODA (fcp-indi, us-east-1), server-side"
  aws s3 cp "$SRC" "$DST/NFBS_Dataset.tar.gz" --source-region us-east-1 --region "$REGION" --copy-props none --only-show-errors
fi
got=$(aws s3api head-object --bucket "${BUCKET#s3://}" --key inputs/fsl-real/NFBS_Dataset.tar.gz \
        --region "$REGION" --query ContentLength --output text)
test "$got" = "$SIZE" || { echo "  NFBS is $got bytes, the RODA object is $SIZE" >&2; exit 1; }
echo "    $got bytes (sha256 asserted on the box)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
echo "$SHA  NFBS_Dataset.tar.gz" > "$tmp/nfbs.sha256"
aws s3 cp "$tmp/nfbs.sha256" "$DST/nfbs.sha256" --region "$REGION" --only-show-errors

for sub in linux-64 linux-aarch64; do
  lock="$DIR/fsl-real-$sub.lock"
  # only the lines recipes/fsl's lock does not already carry
  grep '^https' "$lock" | grep -vxF -f <(grep '^https' "$DIR/../../recipes/fsl/fsl-$sub.lock") | while IFS= read -r line; do
    url="${line%%#*}"; want="${line##*#}"; f="$tmp/$(basename "$url")"
    curl -fsSL --retry 5 -o "$f" "$url"
    have=$(shasum -a 256 "$f" | cut -d' ' -f1)
    [ "$have" = "$want" ] || { echo "  $(basename "$url"): sha256 $have, lock says $want" >&2; exit 1; }
    aws s3 cp "$f" "$DST/pkgs/$sub/$(basename "$url")" --region "$REGION" --only-show-errors
    echo "  $sub: $(basename "$url") verified"
    rm -f "$f"
  done
  aws s3 cp "$lock" "$DST/fsl-real-$sub.lock" --region "$REGION" --only-show-errors
done

for s in run.sh subject.sh; do aws s3 cp "$DIR/$s" "$DST/$s" --region "$REGION" --only-show-errors; done
echo "done."
echo "  $DST/  (NFBS archive, its sha256, fnirt + fugue for both architectures, run.sh, subject.sh)"
