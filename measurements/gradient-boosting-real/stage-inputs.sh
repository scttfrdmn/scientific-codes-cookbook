#!/usr/bin/env bash
# Stage UCI HIGGS (all 11,000,000 rows) and the workload script.
#
# The 2.8 GB file is never downloaded to the machine running this: 00-fetch.task.json fetches it
# from UCI on an AWS box (fetch.py), reads it end to end, and writes it plus fetch.tsv (sha256,
# rows, columns, label counts) to the bucket. This script then checks the bucket copy's recorded
# sha256 against the pin below, and stages real.py with both pins.
#
#   doi:10.24432/C5V312 -- archive.ics.uci.edu/ml/machine-learning-databases/00280/HIGGS.csv.gz,
#   the 2014 file itself (gzip mtime 2014-02-12); the same bytes as HIGGS.csv.gz inside UCI's
#   static/public/280/higgs.zip.
set -euo pipefail

BUCKET="${1:?pass your bucket name}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
PREFIX="s3://$BUCKET/inputs/gradient-boosting-real"
HIGGS_SHA256=ea302c18164d4e3d916a1e2e83a9a8d07069fa6ebc7771e4c0540d54e593b698

if ! aws s3 ls --region "$REGION" "$PREFIX/fetch.tsv" >/dev/null 2>&1; then
  aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/fetch.py" "$PREFIX/fetch.py"
  spawn task run --wait --spec "$(python3 "$REPO/scripts/spec_arch.py" gradient-boosting arm64 \
    "$BUCKET" "$(date +%s)" "$SCRIPT_DIR/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" "$PREFIX/fetch.tsv" - | awk '$1=="sha256"{print $2}')"
test "$got" = "$HIGGS_SHA256" || { echo "HIGGS.csv.gz in the bucket is $got, pin is $HIGGS_SHA256" >&2; exit 1; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$SCRIPT_DIR/real.py" "$tmp/"
{ echo "$HIGGS_SHA256  HIGGS.csv.gz"; (cd "$tmp" && shasum -a 256 real.py); } > "$tmp/pins.sha256"
for f in real.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$tmp/$f" "$PREFIX/$f"
done
cat "$tmp/pins.sha256"
echo "staged to $PREFIX/"
