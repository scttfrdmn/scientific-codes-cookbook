#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and write the cross-chip comparison to
# results/compare.txt. The per-read outputs never leave the box; their sha256 is the evidence.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/kraken2-real/zymo/" "$HERE/results/$chip/"
done
{
  for acc in ERR2984773 ERR2935805; do
    echo "== $acc"
    for chip in arm64 x86_64 amd; do
      echo "$chip $(tr '\n' ' ' < "$HERE/results/$chip/$acc/fingerprints.txt")"
    done
    for chip in x86_64 amd; do
      # the tally is written in awk's hash order, which differs between images: compare it sorted
      for f in report.txt per-read-tally.txt score.tsv checks.txt; do
        if cmp -s <(sort "$HERE/results/arm64/$acc/$f") <(sort "$HERE/results/$chip/$acc/$f"); then
          echo "$f arm64 == $chip"; else echo "$f arm64 != $chip"; fi
      done
    done
  done
} | tee "$HERE/results/compare.txt"
