#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence under results/<chip>/, and write the
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
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/cdo-nco-real/r1/" "$SCR/$chip/"
  tar xzf "$SCR/$chip/logs.tgz" -C "$SCR/$chip" times.tsv host.txt
  for f in cdo-nco-results.tsv check.log times.tsv host.txt; do cp "$SCR/$chip/$f" "$HERE/results/$chip/"; done
done
python3 -I "$HERE/compare.py" "$SCR/arm64" "$SCR/x86_64" "$SCR/amd" | tee "$HERE/results/compare.txt"
