#!/usr/bin/env bash
# Pull the small evidence files of the three chips' runs, the cross-chip comparison and the
# exhaustive-search diagnostic into results/. The alignment tables (220 MB gzipped each) stay in
# the bucket under runs*/foldseek-real/real/aln.sorted.tsv.gz.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in host.txt score.tsv phases.tsv no_hits.txt search.log createdb.log; do
    aws s3 cp --only-show-errors "$BUCKET/$p/foldseek-real/real/$f" "$HERE/results/$chip/$f"
  done
  mkdir -p "$HERE/results/$chip/diag"
  for f in diag.tsv pairs_exhaustive.tsv; do
    aws s3 cp --only-show-errors "$BUCKET/$p/foldseek-real/diag/$f" "$HERE/results/$chip/diag/$f" || true
  done
done
mkdir -p "$HERE/results/compare"
for t in graviton4_vs_amd graviton4_vs_intel; do
  aws s3 cp --only-show-errors "$BUCKET/runs/foldseek-real/compare/$t.txt" "$HERE/results/compare/$t.txt"
  aws s3 cp --only-show-errors "$BUCKET/runs/foldseek-real/compare/$t.only.tsv" "$HERE/results/compare/$t.only.tsv"
done
mkdir -p "$HERE/results/probe"
for f in score.tsv phases.tsv host.txt; do
  aws s3 cp --only-show-errors "$BUCKET/runs/foldseek-real/probe/$f" "$HERE/results/probe/$f"
done
