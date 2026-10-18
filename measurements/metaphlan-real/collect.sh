#!/usr/bin/env bash
# Pull each chip's small evidence files into results/<chip>/ (the mapouts stay in the bucket) and
# write the cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors --exclude '*mapout.bz2' --exclude '*samples.txt' \
    "$BUCKET/$p/metaphlan-real/zymo/" "$HERE/results/$chip/"
done
{
  for acc in ERR2984773 ERR2935805; do
    echo "== $acc"
    for chip in arm64 x86_64 amd; do
      f="$HERE/results/$chip/$acc/fingerprints.txt"
      [ -e "$f" ] && echo "$chip $(tr '\n' ' ' < "$f")"
    done
    for chip in x86_64 amd; do
      a="$HERE/results/arm64/$acc/score.tsv" b="$HERE/results/$chip/$acc/score.tsv"
      [ -e "$b" ] && { cmp -s "$a" "$b" && echo "score.tsv arm64 == $chip" || echo "score.tsv arm64 != $chip"; }
    done
  done
} | tee "$HERE/results/compare.txt"
