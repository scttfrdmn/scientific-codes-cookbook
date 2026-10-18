#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence files under results/<chip>/, and write the
# cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$SCR/$chip" "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/nilearn-real/abide/" "$SCR/$chip/"
  for f in score.tsv host.txt times.txt verify.txt phases.tsv subjects.tsv predictions.tsv; do
    cp "$SCR/$chip/$f" "$HERE/results/$chip/$f"
  done
done
{
  for chip in x86_64 amd; do
    echo "== Graviton4 vs $chip"
    python3 -I "$HERE/compare.py" "$SCR/arm64" "$SCR/$chip"
    echo
  done
  echo "== Intel vs AMD (the same x86 env twin)"
  python3 -I "$HERE/compare.py" "$SCR/x86_64" "$SCR/amd"
} | tee "$HERE/results/compare.txt"
