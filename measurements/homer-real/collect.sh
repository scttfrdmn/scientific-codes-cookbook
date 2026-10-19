#!/usr/bin/env bash
# Pull each chip's small evidence files into results/<chip>/ (the knownResults tables, HOMER's
# logs and the de novo motifs stay in the bucket, except the fixed-input vsoffset table and
# motif1) and write the cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  aws s3 cp --recursive --only-show-errors --exclude '*' \
    --include '*.txt' --include 'staged.sha256' --include 'genome-denovo/motif1.motif' \
    --exclude 'samples.txt' --exclude 'genome.knownResults.txt' --exclude 'offset.knownResults.txt' \
    "$BUCKET/$p/homer-real/whole/" "$HERE/results/$chip/"
done
{
  for f in fingerprints.txt counts.txt known.txt times.txt; do
    echo "== $f"
    for chip in arm64 x86_64 amd; do sed "s/^/$chip  /" "$HERE/results/$chip/$f"; done
  done
  echo "== host.txt"
  for chip in arm64 x86_64 amd; do grep -E 'cpu_model|cpu_part|Thread|thread_siblings' "$HERE/results/$chip/host.txt" | sed "s/^/$chip  /"; done
} | tee "$HERE/results/compare.txt"
