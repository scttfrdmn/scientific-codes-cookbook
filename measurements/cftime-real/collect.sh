#!/usr/bin/env bash
# Pull the three chips' small outputs into results/<chip>/ and compare them.
#
#   bash collect.sh <bucket>
set -euo pipefail
B="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in check.log decode.log; do
    aws s3 cp --only-show-errors "$B/$p/cftime-real/r1/$f" "$HERE/results/$chip/$f"
  done
  for f in summary.tsv timing.tsv host.txt; do
    aws s3 cp --only-show-errors "$B/$p/cftime-real/r1/out/$f" "$HERE/results/$chip/$f"
  done
done
aws s3 cp --only-show-errors "$B/runs/cftime-real/fetch/fetch.log" "$HERE/results/fetch.log"
python3 -I "$HERE/compare.py" "$HERE/results" | tee "$HERE/results/compare.txt"
