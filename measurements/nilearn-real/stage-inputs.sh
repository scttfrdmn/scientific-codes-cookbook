#!/usr/bin/env bash
# Stage nilearn-real: the whole quality-checked ABIDE I Preprocessed cohort (C-PAC, filt_noglobal),
# 871 subjects from 20 sites -- the set `nilearn.datasets.fetch_abide_pcp(quality_checked=True)`
# returns -- plus the CC200 atlas and the analysis scripts.
#
# Tier: RODA. The Preprocessed Connectomes Project release lives in s3://fcp-indi (INDI, AWS Open
# Data, us-east-1). Every object is copied S3-to-S3 with CopyObject (server-side; nothing passes
# through this machine), and S3 computes each copy's full-object SHA256 as it writes it
# (--checksum-algorithm SHA256, a single-request copy, so the checksum is of the whole object, not
# of parts). Those digests are abide.sha256; a first stage writes it, a re-stage must reproduce it,
# and the task checks every staged byte against it with sha256sum -c. A re-stage reads the stored
# checksum (head-object) instead of copying again.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC=fcp-indi
SP=data/Projects/ABIDE_Initiative
DP=inputs/nilearn-real/abide
SCR="$(mktemp -d)"; trap 'rm -rf "$SCR"' EXIT

aws s3 cp --only-show-errors "s3://$SRC/$SP/Phenotypic_V1_0b_preprocessed1.csv" "$SCR/pheno.csv"
# nilearn's own quality_checked filter (nilearn/datasets/func.py, fetch_abide_pcp)
python3 -I - "$SCR/pheno.csv" > "$SCR/ids.txt" <<'PY'
import csv, sys
r = list(csv.DictReader(open(sys.argv[1], newline="")))
ok = [x["FILE_ID"] for x in r if x["FILE_ID"] != "no_filename"
      and x["qc_rater_1"] == "OK" and x["qc_anat_rater_2"] in ("OK", "maybe")
      and x["qc_func_rater_2"] in ("OK", "maybe") and x["qc_anat_rater_3"] == "OK"
      and x["qc_func_rater_3"] == "OK"]
assert len(ok) == len(set(ok)) == 871, len(ok)
print("\n".join(sorted(ok)))
PY
{
  echo "$SP/Phenotypic_V1_0b_preprocessed1.csv Phenotypic_V1_0b_preprocessed1.csv"
  echo "$SP/Resources/cc200_roi_atlas.nii.gz cc200_roi_atlas.nii.gz"
  echo "$SP/Resources/CC200_ROI_labels.csv CC200_ROI_labels.csv"
  while read -r id; do
    echo "$SP/Outputs/cpac/filt_noglobal/func_preproc/${id}_func_preproc.nii.gz func_preproc/${id}_func_preproc.nii.gz"
    echo "$SP/Outputs/cpac/filt_noglobal/func_mask/${id}_func_mask.nii.gz func_mask/${id}_func_mask.nii.gz"
    echo "$SP/Outputs/cpac/filt_noglobal/rois_cc200/${id}_rois_cc200.1D rois_cc200/${id}_rois_cc200.1D"
  done < "$SCR/ids.txt"
} > "$SCR/plan.txt"
echo "$(wc -l < "$SCR/plan.txt" | tr -d " ") objects to stage"

copy_one() {   # src-key dst-rel  ->  "<sha256hex>  <dst-rel>"; an object already staged is not re-copied
  b64=$(aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$DP/$2" --checksum-mode ENABLED \
        --query ChecksumSHA256 --output text 2>/dev/null) || b64=""
  case "$b64" in ""|None) b64=$(aws s3api copy-object --region "$REGION" --copy-source "$SRC/$1" --bucket "$BUCKET" \
        --key "$DP/$2" --checksum-algorithm SHA256 --query CopyObjectResult.ChecksumSHA256 --output text) \
    || { echo "FAIL $2" >&2; return 1; } ;; esac
  printf '%s  %s\n' "$(printf '%s' "$b64" | base64 -d | od -An -tx1 | tr -d ' \n')" "$2"
}
export -f copy_one; export SRC BUCKET REGION DP
xargs -P 32 -L 1 bash -c 'copy_one "$0" "$1"' < "$SCR/plan.txt" | LC_ALL=C sort -k2 > "$SCR/abide.sha256"
[ "$(wc -l < "$SCR/abide.sha256")" = "$(wc -l < "$SCR/plan.txt")" ] || { echo "FAIL: not every object copied" >&2; exit 1; }
if [ -s "$HERE/abide.sha256" ]; then
  cmp "$SCR/abide.sha256" "$HERE/abide.sha256" || { echo "FAIL: staged bytes differ from abide.sha256" >&2; exit 1; }
  echo "all $(wc -l < "$HERE/abide.sha256" | tr -d " ") objects match abide.sha256"
else
  cp "$SCR/abide.sha256" "$HERE/abide.sha256"; echo "wrote abide.sha256 (first stage)"
fi
aws s3 cp --region "$REGION" --only-show-errors "$HERE/abide.sha256" "s3://$BUCKET/$DP/abide.sha256"

( cd "$HERE" && grep -E ' (real\.py|real\.sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "s3://$BUCKET/inputs/nilearn-real/$f"
done
echo "staged to s3://$BUCKET/inputs/nilearn-real/"
