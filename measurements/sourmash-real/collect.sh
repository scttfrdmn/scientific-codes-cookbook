#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ (not the signatures: their content hash is the
# evidence) and write the cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors --exclude '*.sig.zip' --exclude '*matches.zip' \
    "$BUCKET/$p/sourmash-real/zymo/" "$HERE/results/$chip/"
  # a sketch log is a progress counter; keep its last lines (the sequence count and the file written)
  for f in "$HERE"/results/$chip/*/sketch_*.log; do
    tr '\r' '\n' < "$f" | tr -d '\033' | sed 's/\[K//g' | grep -v '^ *$' | tail -3 > "$f.tmp" && mv "$f.tmp" "$f"
  done
done
{
  for acc in ERR2984773 ERR2935805; do
    echo "== $acc"
    for chip in arm64 x86_64 amd; do
      echo "$chip $(tr '\n' ' ' < "$HERE/results/$chip/$acc/fingerprints.txt")"
    done
    for chip in x86_64 amd; do
      for f in gather.csv $acc.summarized.csv species.tsv checks.txt; do
        if cmp -s "$HERE/results/arm64/$acc/$f" "$HERE/results/$chip/$acc/$f"; then
          echo "$f arm64 == $chip"; else echo "$f arm64 != $chip"; fi
      done
    done
  done
} | tee "$HERE/results/compare.txt"
