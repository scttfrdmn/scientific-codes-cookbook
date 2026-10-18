#!/usr/bin/env bash
# Stage PXD083239 (PRIDE, CC0) -- all six Orbitrap Exploris 480 HeLa DDA runs as the submitters'
# own mzML, the Swiss-Prot human FASTA they searched, and their published 1%-FDR PSMs -- without
# the data ever touching this machine: stage.py runs in a spawn task that fetches from PRIDE and
# writes to the bucket (6.4 GB in, 3.3 GB kept).
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail

BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/proteomics-real"
RUNS="GFP_C001_1 GFP_C001_2 GFP_C001_3 MCL1_c007_1 MCL1_c007_2 MCL1_c007_3"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

up () { aws s3 cp --region "$REGION" --only-show-errors "$1" "$DST/$2"; }

if ! aws s3 ls "$DST/derived.sha256" --region "$REGION" >/dev/null 2>&1; then
  cp "$HERE/stage.py" .; shasum -a 256 stage.py > stage.sha256
  up stage.py stage.py; up stage.sha256 stage.sha256
  sed "s|\${COOKBOOK_BUCKET}|$BUCKET|g" "$HERE/stage.task.json" > stage.json
  spawn task run --spec stage.json --wait
fi

echo "== PRIDE served the bytes this measurement was made on =="
aws s3 cp --region "$REGION" --only-show-errors "$DST/source.sha256" .
aws s3 cp --region "$REGION" --only-show-errors "$DST/derived.sha256" .
diff "$HERE/source.sha256" source.sha256 || { echo "PRIDE's files differ from the pinned ones" >&2; exit 1; }
diff "$HERE/derived.sha256" derived.sha256 || { echo "the staged inputs differ from the pinned ones" >&2; exit 1; }

cp "$HERE/search.sh" "$HERE/rescore.sh" .
{ cat derived.sha256; shasum -a 256 search.sh rescore.sh; } > pins.sha256
echo "$RUNS" | tr ' ' '\n' > runs.txt
echo "GFP_C001_1" > runs-probe.txt
for f in search.sh rescore.sh pins.sha256 runs.txt runs-probe.txt; do up "$f" "$f"; done
cat pins.sha256
echo "staged to $DST/"
