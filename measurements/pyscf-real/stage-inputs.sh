#!/usr/bin/env bash
# Stage measurements/pyscf-real: QM9 is fetched on AWS by 00-fetch.task.json (figshare collection
# 10.6084/m9.figshare.c.978904, each file checked against figshare's computed_md5, then sha256'd into
# qm9.sha256 beside it in the bucket). This script checks those pins and stages the scripts.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/pyscf-real"
WANT_TAR=3a63848ac80691bdb8d41834b575afad345b9300d7a2db0c38adb7f6eaa8360c
WANT_UNC=3aa5115d540b356de94791d4a74c3bf1ed91c469ecf52a4f5d7cc0506fe02e24
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/qm9/qm9.sha256" -)" \
  || { echo "no $DST/qm9/qm9.sha256 -- run 00-fetch.task.json first" >&2; exit 1; }
echo "$got" | grep -q "^$WANT_TAR  dsgdb9nsd.xyz.tar.bz2$" && echo "$got" | grep -q "^$WANT_UNC  uncharacterized.txt$" \
  || { echo "FAIL: the fetched QM9 files are not the pinned ones" >&2; exit 1; }
for f in run.sh qm9.py pyscf_run.py psi4_run.py; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged $DST/"
