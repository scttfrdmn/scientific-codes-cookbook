#!/usr/bin/env bash
# Pull each chip's small evidence files into results/<chip>/ and write results/compare.txt.
# The gene-family tables (14 MB each) stay out of git; the comparison reads them from a scratch dir.
#
#   bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
S="${2:?pass a scratch dir}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors --exclude '*genefamilies.tsv' \
    "$BUCKET/$p/humann-real/zymo/ERR2984773/" "$HERE/results/$chip/"
  aws s3 cp --recursive --only-show-errors --exclude '*' --include '*_genefamilies.tsv' --include '*_path*.tsv' \
    "$BUCKET/$p/humann-real/zymo/ERR2984773/" "$S/$chip/"
done
{
  echo "== Intel vs AMD (same image)"; python3 "$HERE/compare.py" "$S/x86_64" "$S/amd"
  echo "== Graviton4 vs Intel"; python3 "$HERE/compare.py" "$S/arm64" "$S/x86_64"
  echo "== Graviton4 vs AMD"; python3 "$HERE/compare.py" "$S/arm64" "$S/amd"
} | tee "$HERE/results/compare.txt"
