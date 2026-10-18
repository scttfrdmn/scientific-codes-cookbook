#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence files under results/<chip>/, and write the
# comparisons to results/compare.txt.
#
#   bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$SCR/$chip" "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/iqtree-real/pf01255/" "$SCR/$chip/"
  for f in iqtree/checks.txt iqtree/summary.txt iqtree/times.txt iqtree/host.txt iqtree/modelfinder.tsv \
           iqtree/real.treefile eval/passes.txt eval/eval-check.txt raxml/crosscheck.txt; do
    cp "$SCR/$chip/$f" "$HERE/results/$chip/$(basename "$f")"
  done
  [ -e "$SCR/$chip/eval-g4tree/passes.txt" ] && cp "$SCR/$chip/eval-g4tree/passes.txt" "$HERE/results/$chip/g4tree-passes.txt"
done
{
  for chip in x86_64 amd; do
    echo "== Graviton4 vs $chip (task 1)"
    python3 -I "$HERE/compare.py" "$SCR/arm64/iqtree" "$SCR/$chip/iqtree"
  done
  echo "== AMD vs AMD again: ModelFinder alone, same image, seed and threads (05-mf-repeat)"
  python3 -I "$HERE/compare.py" --models "$SCR/amd/iqtree/real.model.gz" "$SCR/amd/mf-repeat/mf.model.gz"
  echo "== Graviton4's tree, LG+G4 fixed point, every chip (02-eval on arm64, 04-eval-g4tree on x86)"
  for chip in arm64 x86_64 amd; do
    f="$SCR/$chip/eval-g4tree/evconv.iqtree"; [ "$chip" = arm64 ] && f="$SCR/arm64/eval/evconv.iqtree"
    echo "$chip $(awk '/^Log-likelihood of the tree:/{print $5}' "$f")  $(shasum -a 256 "${f%.iqtree}.treefile" | cut -c1-16)"
  done
} | tee "$HERE/results/compare.txt"
