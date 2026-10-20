#!/usr/bin/env bash
# Stage squidpy-real: two whole spatial sections from the CELLxGENE Census LTS release, plus the
# scripts the task runs.
#
#   slideseq  88b7da92-178d-4f27-bc18-b0165b256c67  Slide-seqV2, healthy human kidney, Puck_200903_13:
#             44,588 beads (Marshall et al., iScience 2022, doi:10.1016/j.isci.2022.104097)
#   visium    05d4a216-5384-4724-bd2a-2ea38f02e7cc  10x Visium, human myocardial infarction, RZ_BZ_P12:
#             one capture area (Kuppe et al., Nature 2022, doi:10.1038/s41586-022-05060-x)
#
# Tier: RODA. Census LTS 2025-01-30 is `do_not_delete` in its release.json. Each object is copied
# S3-to-S3 (server-side CopyObject; nothing passes through this machine) after its size and ETag
# match the pins below, and S3 computes the copy's full-object SHA256 as it writes it. Those digests
# are inputs.sha256; a first stage writes it, a re-stage must reproduce it, and the task checks the
# staged bytes against it before reading one.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRCB=cellxgene-census-public-us-west-2
SRCP=cell-census/2025-01-30/h5ads
DP=inputs/squidpy-real
SCR="$(mktemp -d)"; trap 'rm -rf "$SCR"' EXIT

# name  dataset_id  size  ETag
cat > "$SCR/plan.txt" <<'PLAN'
slideseq 88b7da92-178d-4f27-bc18-b0165b256c67 90926645 "0968e4bff50f867c0de991b631fdb610-11"
visium 05d4a216-5384-4724-bd2a-2ea38f02e7cc 197717507 "7ecd352f083e859e49970f96ced05774-24"
PLAN

: > "$SCR/inputs.sha256"
while read -r name id size etag; do
  key="$DP/$name.h5ad"
  b64=$(aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$key" --checksum-mode ENABLED \
        --query ChecksumSHA256 --output text 2>/dev/null) || b64=""
  case "$b64" in ""|None)
    got=$(aws s3api head-object --no-sign-request --region us-west-2 --bucket "$SRCB" --key "$SRCP/$id.h5ad" \
          --query '[ContentLength,ETag]' --output text)
    test "$got" = "$size	$etag" || { echo "Census object $id is '$got', expected '$size $etag'" >&2; exit 1; }
    b64=$(aws s3api copy-object --region "$REGION" --copy-source "$SRCB/$SRCP/$id.h5ad" --bucket "$BUCKET" \
          --key "$key" --checksum-algorithm SHA256 --query CopyObjectResult.ChecksumSHA256 --output text) ;;
  esac
  printf '%s  %s.h5ad\n' "$(printf '%s' "$b64" | base64 -d | od -An -tx1 | tr -d ' \n')" "$name" >> "$SCR/inputs.sha256"
done < "$SCR/plan.txt"

if [ -s "$HERE/inputs.sha256" ]; then
  cmp "$SCR/inputs.sha256" "$HERE/inputs.sha256" || { echo "FAIL: staged bytes differ from inputs.sha256" >&2; exit 1; }
  echo "both objects match inputs.sha256"
else
  cp "$SCR/inputs.sha256" "$HERE/inputs.sha256"; echo "wrote inputs.sha256 (first stage)"
fi

files="real.py real.sh"
( cd "$HERE" && { sha256sum $files 2>/dev/null || shasum -a 256 $files; } ) > "$SCR/scripts.sha256"
cat "$HERE/inputs.sha256" "$SCR/scripts.sha256" > "$HERE/pins.sha256"
for f in $files pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "s3://$BUCKET/$DP/$f"
done
# the tie diagnostic (ties.task.json pins it inline)
aws s3 cp --region "$REGION" --only-show-errors "$HERE/ties.py" "s3://$BUCKET/$DP/ties.py"
cat "$HERE/pins.sha256"
echo "staged to s3://$BUCKET/$DP/"
