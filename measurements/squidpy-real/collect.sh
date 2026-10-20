#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence files under results/<chip>/, and write the
# cross-chip comparison to results/compare.txt.
#
#   [PYTHON=<python with numpy+pandas>] bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$SCR/$chip"
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/squidpy-real/real/" "$SCR/$chip/"
  for s in slideseq visium; do
    mkdir -p "$HERE/results/$chip/$s"
    for f in score.tsv phases.tsv; do cp "$SCR/$chip/r1/$s/$f" "$HERE/results/$chip/$s/$f"; done
  done
  for f in host.txt times.txt replicate.txt; do cp "$SCR/$chip/$f" "$HERE/results/$chip/$f"; done
done
{
  for chip in x86_64 amd; do
    echo "== Graviton4 vs $chip"
    "${PYTHON:-python3}" -I "$HERE/compare.py" "$SCR/arm64/r1" "$SCR/$chip/r1"
    echo
  done
  echo "== Intel vs AMD (the same x86 env twin)"
  "${PYTHON:-python3}" -I "$HERE/compare.py" "$SCR/x86_64/r1" "$SCR/amd/r1"
} | tee "$HERE/results/compare.txt"
