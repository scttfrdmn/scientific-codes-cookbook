#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and write the cross-chip comparison to
# results/compare.txt (sha256 of every Bracken output, and the checks).
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/abundance-real/zymo/" "$HERE/results/$chip/"
done
{
  for acc in ERR2984773 ERR2935805; do
    echo "== $acc"
    for chip in x86_64 amd; do
      for f in S.bracken S.breport G.bracken G.breport checks.txt score.tsv; do
        if cmp -s "$HERE/results/arm64/$acc/$f" "$HERE/results/$chip/$acc/$f"; then
          echo "$f arm64 == $chip"; else echo "$f arm64 != $chip"; fi
      done
    done
  done
} | tee "$HERE/results/compare.txt"
