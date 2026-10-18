#!/usr/bin/env bash
# Stage measurements/neuroimaging-real: the NFBS cohort's diffusion scans, dtifit, and the scripts.
#
# THE DATA (RODA tier). The Enhanced Rockland Sample's baseline (ses-BAS1) DIFF_137_AP diffusion
# scan -- Siemens TrioTim, 2 mm isotropic, 9 b=0 + 128 directions at b=1500 -- for each of the 125
# NFBS subjects (measurements/fsl-real) that has one: 123, listed in subjects.txt. INDI's bucket on
# AWS Open Data, written 2019-10-31. The object path says "Latest", so the pin is not the path: it
# is dwi.sha256 (all 492 files), computed on AWS by pin.task.json from the staged copies and
# asserted again on every box before any compute.
#
# THE CROSS-CHECK'S TOOL. recipes/fsl must be staged first (`make stage RECIPE=fsl`): its 89
# packages per architecture are reused as-is, plus fsl-fdt (dtifit) from FSL's channel, verified
# against fsl-dtifit-linux-*.lock, which is the recipe lock plus that one line.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DIR="$(cd "$(dirname "$0")" && pwd)"
DST="$BUCKET/inputs/neuroimaging-real"
SRC=s3://fcp-indi/data/Projects/RocklandSample/RawDataBIDSLatest

aws s3 ls "$BUCKET/inputs/fsl/pkgs/linux-aarch64/" --region "$REGION" >/dev/null \
  || { echo "stage recipes/fsl first: make stage RECIPE=fsl" >&2; exit 1; }

have=$( { aws s3 ls "$DST/dwi/" --region "$REGION" || true; } | awk '{print $4}' | sort)
copy() {  # <subject> <ext>: server-side, cross-region, skipped if already staged
  local f="sub-$1_ses-BAS1_dwi.$2"
  grep -qxF "$f" <<<"$have" && return 0
  aws s3 cp "$SRC/sub-$1/ses-BAS1/dwi/$f" "$DST/dwi/$f" --source-region us-east-1 --region "$REGION" \
    --copy-props none --only-show-errors
}
n=0
while read -r id; do
  for ext in nii.gz bval bvec json; do copy "$id" "$ext" & done
  n=$((n+1)); [ $((n % 4)) -eq 0 ] && wait
done < "$DIR/subjects.txt"
wait
got=$(aws s3 ls "$DST/dwi/" --region "$REGION" | wc -l | tr -d ' ')
want=$(( $(wc -l < "$DIR/subjects.txt") * 4 ))
test "$got" = "$want" || { echo "  staged $got files, expected $want" >&2; exit 1; }
echo "  $got DWI files staged ($(wc -l < "$DIR/subjects.txt" | tr -d ' ') subjects)"
[ -f "$DIR/dwi.sha256" ] && aws s3 cp "$DIR/dwi.sha256" "$DST/dwi.sha256" --region "$REGION" --only-show-errors

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for sub in linux-64 linux-aarch64; do
  lock="$DIR/fsl-dtifit-$sub.lock"
  grep '^https' "$lock" | grep -vxF -f <(grep '^https' "$DIR/../../recipes/fsl/fsl-$sub.lock") | while IFS= read -r line; do
    url="${line%%#*}"; want="${line##*#}"; f="$tmp/$(basename "$url")"
    curl -fsSL --retry 5 -o "$f" "$url"
    sha=$(shasum -a 256 "$f" | cut -d' ' -f1)
    [ "$sha" = "$want" ] || { echo "  $(basename "$url"): sha256 $sha, lock says $want" >&2; exit 1; }
    aws s3 cp "$f" "$DST/pkgs/$sub/$(basename "$url")" --region "$REGION" --only-show-errors
    echo "  $sub: $(basename "$url") verified"
    rm -f "$f"
  done
  aws s3 cp "$lock" "$DST/fsl-dtifit-$sub.lock" --region "$REGION" --only-show-errors
done

for s in subjects.txt fsl.sh dipy.sh subject.py compare.py; do
  [ -f "$DIR/$s" ] && aws s3 cp "$DIR/$s" "$DST/$s" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $DST/  (123 DWI scans, dtifit for both architectures, the scripts)"
