#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence files under results/<chip>/, and write the
# split checks and the cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  for sub in raxml iqtree; do
    aws s3 cp --recursive --only-show-errors "$BUCKET/$p/raxml-ng-real/pf01255/$sub/" "$SCR/$chip/$sub/"
  done
  mkdir -p "$HERE/results/$chip"
  for f in host.txt aln-shape.txt checks.txt counts.txt times.txt rx.raxml.bestTree sup.raxml.supportFBP; do
    cp "$SCR/$chip/raxml/$f" "$HERE/results/$chip/"
  done
  cp "$SCR/$chip/iqtree/crosscheck.txt" "$HERE/results/$chip/iqtree-crosscheck.txt"
  cp "$SCR/$chip/iqtree/passes.txt" "$HERE/results/$chip/iqtree-passes.txt"
done
python3 -I "$HERE/compare.py" "$SCR" | tee "$HERE/results/compare.txt"
