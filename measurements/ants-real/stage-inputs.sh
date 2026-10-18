#!/usr/bin/env bash
# Stage measurements/ants-real. Nothing new is downloaded:
#
# THE DATA (RODA tier) is measurements/fsl-real's NFBS cohort, read where fsl-real staged it
# (inputs/fsl-real/NFBS_Dataset.tar.gz, sha256 asserted on the box), so ANTs and FSL register the
# same bytes. Stage it first: measurements/fsl-real/stage-inputs.sh <bucket>.
#
# THE TEMPLATE is FSL's MNI152 2 mm brain and brain mask, out of fsl-data_standard 2208.0 -- the
# package recipes/fsl's lock pins by sha256 and fsl-real registered to. `make stage RECIPE=fsl`
# puts the package in the bucket; task 00-template unpacks the two files on AWS (the ANTs image
# has no bzip2) and this script then checks them against mni.sha256, committed here.
#   stage-inputs.sh <bucket>            scripts + checks
#   stage-inputs.sh <bucket> template   also print the 00-template spec to launch first
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DIR="$(cd "$(dirname "$0")" && pwd)"
DST="$BUCKET/inputs/ants-real"

got=$(aws s3api head-object --bucket "${BUCKET#s3://}" --key inputs/fsl-real/NFBS_Dataset.tar.gz \
        --region "$REGION" --query ContentLength --output text 2>/dev/null) || got=none
test "$got" = 1751464473 || { echo "stage NFBS first: measurements/fsl-real/stage-inputs.sh $BUCKET" >&2; exit 1; }
aws s3 ls "$BUCKET/inputs/fsl/pkgs/linux-64/fsl-data_standard-2208.0-0.tar.bz2" --region "$REGION" >/dev/null \
  || { echo "stage recipes/fsl first: make stage RECIPE=fsl" >&2; exit 1; }

for s in template.sh run.sh subject.sh mni.sha256; do aws s3 cp "$DIR/$s" "$DST/$s" --region "$REGION" --only-show-errors; done

if [ "${2:-}" = template ]; then
  sed "s|\${COOKBOOK_BUCKET}|${BUCKET#s3://}|g" "$DIR/00-template.task.json" > "${TMPDIR:-/tmp}/ants-real-template.json"
  echo "launch: spawn task run --spec ${TMPDIR:-/tmp}/ants-real-template.json --wait"
  exit 0
fi

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for f in MNI152_T1_2mm_brain.nii.gz MNI152_T1_2mm_brain_mask.nii.gz; do
  aws s3 cp "$DST/$f" "$tmp/$f" --region "$REGION" --only-show-errors \
    || { echo "  $f is not staged: run task 00-template (stage-inputs.sh <bucket> template)" >&2; exit 1; }
done
(cd "$tmp" && shasum -a 256 -c "$DIR/mni.sha256")
echo "done.  $DST/  (MNI152 2 mm brain + mask, their sha256, run.sh, subject.sh)"
