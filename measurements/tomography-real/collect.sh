#!/usr/bin/env bash
# Pull the three chips' small evidence files into results/<chip>/, then run compare.py ON AWS (the
# saved slices are 26 MB each, so they never come to this machine) and keep its report.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in host.txt times.txt real.log; do
    aws s3 cp --only-show-errors "s3://$BUCKET/$p/tomography-real/real/$f" "$HERE/results/$chip/$f"
  done
  for f in score.tsv slices.tsv; do
    aws s3 cp --only-show-errors "s3://$BUCKET/$p/tomography-real/real/r1/$f" "$HERE/results/$chip/$f"
  done
done
aws s3 cp --only-show-errors "$HERE/compare.py" "s3://$BUCKET/inputs/tomography-real/compare.py"
spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" tomography arm64 "$BUCKET" "$(date +%s)" "$HERE/02-compare.task.json")"
aws s3 cp --only-show-errors "s3://$BUCKET/runs/tomography-real/compare/compare.txt" "$HERE/results/compare.txt"
cat "$HERE/results/compare.txt"
