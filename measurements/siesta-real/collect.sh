#!/usr/bin/env bash
# Pull the three chips' outputs into results/<chip>/ and write the cross-chip comparison to
# results/compare.txt.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in real.log host.txt times.txt siesta-header.txt suite.tsv digest.tsv qdot.tsv; do
    aws s3 cp --only-show-errors "$BUCKET/$p/siesta-real/suite/$f" "$HERE/results/$chip/$f"
  done
done
{
  for chip in x86_64 amd; do
    echo "== Graviton4 vs $chip"
    python3 -I "$HERE/compare.py" "$HERE/results/arm64" "$HERE/results/$chip"
  done
  echo "== Intel vs AMD (the same x86 env twin)"
  python3 -I "$HERE/compare.py" "$HERE/results/x86_64" "$HERE/results/amd"
} | tee "$HERE/results/compare.txt"
