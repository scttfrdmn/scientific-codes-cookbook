#!/usr/bin/env bash
# Stage the homer-real inputs:
#   ENCFF796WRU.bed.gz   ENCODE CTCF ChIP-seq, GM12878 (ENCSR000DZN), conservative IDR thresholded
#                        peaks on GRCh38, replicates 1+2 -- the experiment's preferred-default peak set.
#                        711,745 B, ENCODE md5 3f36655eb9a421a542ff031fc7b5e9aa
#   GRCh38 no-alt        minimap2-real's 195-sequence analysis set (inputs/minimap2-real/), reused --
#                        the assembly ENCODE called these peaks on
#
# Tier: RODA. The peak file is copied server-side from the ENCODE bucket on AWS Open Data
# (s3://encode-public, us-west-2) into the bucket -- nothing passes through this machine -- and
# checked against ENCODE's own md5 on the box, next to the bytes. Every other staged object is
# pinned by sha256 in pins.sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/homer-real"
SRC=s3://encode-public/2020/09/25/7e8d6767-53ef-45db-93e9-dcc111ebd38d/ENCFF796WRU.bed.gz

aws s3 ls --region "$REGION" "$BUCKET/inputs/minimap2-real/GRCh38_no_alt_analysis_set.fa" >/dev/null \
  || { echo "stage minimap2-real's GRCh38 first: measurements/minimap2-real/stage-inputs.sh" >&2; exit 1; }
curl -sSf -H 'Accept: application/json' 'https://www.encodeproject.org/files/ENCFF796WRU/?frame=object' \
  | python3 -c 'import json,sys; f=json.load(sys.stdin); print("ENCODE:", f["accession"], f["md5sum"], f["file_size"], f["output_type"], f["assembly"], f["dataset"], f["status"])'
aws s3 ls --region "$REGION" "$P/ENCFF796WRU.bed.gz" >/dev/null 2>&1 \
  || aws s3 cp --only-show-errors --region "$REGION" "$SRC" "$P/ENCFF796WRU.bed.gz"
for f in homer-real.sh pins.sha256 encode.md5; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
echo "staged under $P/"
