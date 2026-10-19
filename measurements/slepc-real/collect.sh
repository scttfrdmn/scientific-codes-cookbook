#!/usr/bin/env bash
# Pull the three chips' outputs into results/<chip>/ and write the cross-chip comparison to
# results/compare.txt.
#
#   bash collect.sh <bucket> [N]
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
N="${2:-128}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$HERE/results/$chip/N$N"
  aws s3 cp --only-show-errors "$BUCKET/$p/slepc-real/real/host.txt" "$HERE/results/$chip/host.txt"
  for f in real.log score.tsv eigenvalues.tsv times.txt; do
    aws s3 cp --only-show-errors "$BUCKET/$p/slepc-real/real/N$N/$f" "$HERE/results/$chip/N$N/$f"
  done
done
{
  for chip in x86_64 amd; do
    echo "== Graviton4 vs $chip"
    python3 -I "$HERE/compare.py" "$HERE/results/arm64/N$N" "$HERE/results/$chip/N$N"
  done
  echo "== Intel vs AMD (the same x86 env twin)"
  python3 -I "$HERE/compare.py" "$HERE/results/x86_64/N$N" "$HERE/results/amd/N$N"
} | tee "$HERE/results/compare.txt"
