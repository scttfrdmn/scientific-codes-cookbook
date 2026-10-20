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
  for f in arviz_real.log host.txt times.txt draws.tsv reference_diag.tsv fit_vs_stansummary.tsv; do
    aws s3 cp --only-show-errors "$BUCKET/$p/arviz-real/pdb/$f" "$HERE/results/$chip/$f"
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
# the per-scalar table is 1.5 MB a chip; compare.py reads the .gz as well
for chip in arm64 x86_64 amd; do gzip -nf "$HERE/results/$chip/fit_vs_stansummary.tsv"; done
