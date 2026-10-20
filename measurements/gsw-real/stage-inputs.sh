#!/usr/bin/env bash
# Stage the whole Argo GDAC profile archive (every daily file, 1997 to the staging date), plus
# the GDAC's profile index and the workload script, into your bucket. Server-side S3-to-S3:
# nothing touches the local disk.
#
# Source: the Argo GDAC on the AWS Open Data Registry, s3://argo-gdac-sandbox/pub/geo/
# (eu-west-3; public, read with any credentials). geo/<ocean>/<YYYY>/<MM>/<YYYYMMDD>_prof.nc holds
# every Argo profile of one day in one ocean (Atlantic, Pacific, Indian): core P/T/S, real-time
# and delayed-mode. As staged on 2026-10-20: 30,229 objects, 215.0 GB.
#
# THE GDAC IS A LIVE ARCHIVE. These files are rebuilt whenever a float's delayed-mode QC lands
# (all 1,095 files of 2023 were rewritten in the 30 days before staging), so a pin taken from the
# source goes stale. Each object is copied with S3's own full-object SHA256 (CopyObject with
# ChecksumAlgorithm=SHA256, single-part: every file is < 5 GB), so the pin costs no download. The
# result is compared with the committed argo.sha256; a mismatch means the GDAC has revised that
# file since 2026-10-20, and the script fails unless REPIN=1, which accepts the current bytes.
# The citable frozen equivalent is the monthly SEANOE snapshot, doi:10.17882/42182 (2026-10-14:
# #134998, 89 GB tar.gz), whose dac/ tree holds the per-cycle files these days aggregate.
#
#   bash stage-inputs.sh <bucket>                 # copy + pin (or verify against argo.sha256)
#   SCRIPT_ONLY=1 bash stage-inputs.sh <bucket>   # re-stage argo.py + pins only
set -euo pipefail

BUCKET="${1:?pass your bucket}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DST="inputs/gsw-real"
SRC_BUCKET=argo-gdac-sandbox
PINS="$SCRIPT_DIR/argo.sha256"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if [ "${SCRIPT_ONLY:-0}" != 1 ]; then
  for o in atlantic_ocean pacific_ocean indian_ocean; do
    aws s3api list-objects-v2 --bucket "$SRC_BUCKET" --prefix "pub/geo/$o/" \
      --query 'Contents[].[Key,ETag,LastModified,Size]' --output text
  done | grep '_prof.nc' | sort > "$tmp/src.txt"
  # The GDAC's profile index, snapshotted alongside: an independent count of profiles.
  aws s3api list-objects-v2 --bucket "$SRC_BUCKET" --prefix pub/idx/ar_index_global_prof.txt.gz \
    --query 'Contents[].[Key,ETag,LastModified,Size]' --output text >> "$tmp/src.txt"
  n=$(wc -l < "$tmp/src.txt" | tr -d ' ')
  echo "listed $n objects"

  copy_one() {   # key -> "sha256hex  argo/<ocean>/<YYYY>/<MM>/<file>"
    key="$1"
    case "$key" in
      pub/idx/*) rel="argo/$(basename "$key")" ;;
      *) rel="argo/${key#pub/geo/}" ;;
    esac
    b64=$(aws s3api copy-object --copy-source "$SRC_BUCKET/$key" --bucket "$BUCKET" \
            --key "$DST/$rel" --checksum-algorithm SHA256 --region us-west-2 \
            --query 'CopyObjectResult.ChecksumSHA256' --output text)
    echo "$(echo "$b64" | base64 -d | xxd -p -c 64)  $rel"
  }
  export -f copy_one; export SRC_BUCKET BUCKET DST
  cut -f1 "$tmp/src.txt" | xargs -P 64 -I{} bash -c 'copy_one "$@"' _ {} | sort -k2 > "$tmp/got.sha256"
  [ "$(awk 'length($1) == 64' "$tmp/got.sha256" | wc -l | tr -d ' ')" = "$n" ] \
    || { echo "FAIL: not every object copied with a checksum"; exit 1; }

  if [ -f "$PINS" ] && [ "${REPIN:-0}" != 1 ]; then
    bad=$(diff "$PINS" "$tmp/got.sha256" | grep -c '^[<>]' || true)
    [ "$bad" = 0 ] || { echo "FAIL: $bad lines differ from argo.sha256 -- the GDAC revised files since pinning (REPIN=1 to accept)"; exit 1; }
    echo "all $n match argo.sha256"
  else
    cp "$tmp/got.sha256" "$PINS"
    gzip -9n < "$tmp/src.txt" > "$SCRIPT_DIR/argo.source.tsv.gz"
    echo "pinned $n objects -> argo.sha256 (source Key/ETag/LastModified/Size in argo.source.tsv.gz)"
  fi
fi

cp "$PINS" "$tmp/pins.sha256"
( cd "$SCRIPT_DIR" && shasum -a 256 argo.py ) >> "$tmp/pins.sha256"
aws s3 cp --only-show-errors "$SCRIPT_DIR/argo.py" "s3://$BUCKET/$DST/argo.py"
aws s3 cp --only-show-errors "$SCRIPT_DIR/compare.py" "s3://$BUCKET/$DST/compare.py"   # sha256 pinned in compare.task.json
aws s3 cp --only-show-errors "$tmp/pins.sha256" "s3://$BUCKET/$DST/pins.sha256"
echo "staged to s3://$BUCKET/$DST/"
