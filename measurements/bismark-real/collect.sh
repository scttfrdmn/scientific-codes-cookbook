#!/usr/bin/env bash
# Pull each chip's small evidence files into results/<chip>/ (BAMs, bedGraphs and coverage stay in
# the bucket) and write the cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors --exclude '*.bam' --exclude '*.bai' --exclude '*.gz' \
    --exclude '*samples*' "$BUCKET/$p/bismark-real/whole/" "$HERE/results/$chip/"
done
{
  for f in bismark/fingerprints.txt sort/fingerprints-sort.txt bwameth/fingerprints-bwameth.txt call/fingerprints-call.txt \
           bismark/contexts.txt bwameth/times-bwameth.txt call/callers.txt call/aligners.txt; do
    echo "== $f"
    for chip in arm64 x86_64 amd; do
      [ -e "$HERE/results/$chip/$f" ] && sed "s/^/$chip  /" "$HERE/results/$chip/$f" | grep -v -e wall_s -e peak_anon
    done
  done
} | tee "$HERE/results/compare.txt"
