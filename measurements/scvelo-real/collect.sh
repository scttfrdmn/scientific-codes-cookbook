#!/usr/bin/env bash
# Pull the three chips' outputs (and the arm64 sizing probe), keep the small evidence files under
# results/<chip>/, and write the cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket> <scratch dir>        # needs a python3 with numpy
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$SCR/$chip" "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/scvelo-real/real/" "$SCR/$chip/"
  for f in host.txt times.txt real.log; do cp "$SCR/$chip/$f" "$HERE/results/$chip/$f"; done
  for f in score.tsv phases.tsv fit_var.tsv hvg.txt; do cp "$SCR/$chip/r1/$f" "$HERE/results/$chip/$f"; done
done
mkdir -p "$SCR/probe" "$HERE/results/probe"
aws s3 cp --recursive --only-show-errors "$BUCKET/runs/scvelo-real/probe/" "$SCR/probe/"
for f in score.tsv phases.tsv; do cp "$SCR/probe/r1/$f" "$HERE/results/probe/$f"; done
{
  for chip in x86_64 amd; do
    echo "== Graviton4 vs $chip"
    python3 -I "$HERE/compare.py" "$SCR/arm64/r1" "$SCR/$chip/r1"
    echo
  done
  echo "== Intel vs AMD (the same x86 env twin)"
  python3 -I "$HERE/compare.py" "$SCR/x86_64/r1" "$SCR/amd/r1"
  echo
  echo "== Graviton4: the 64-gene sizing probe vs the full run (a gene's fit must not depend on the others)"
  python3 -I "$HERE/compare.py" "$SCR/probe/r1" "$SCR/arm64/r1"
} | tee "$HERE/results/compare.txt"
