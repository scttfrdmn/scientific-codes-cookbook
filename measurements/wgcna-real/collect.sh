#!/usr/bin/env bash
# Pull the three chips' outputs, keep the small evidence files under results/<chip>/, and write the
# cross-chip comparison to results/compare.txt.
#
#   bash collect.sh <bucket> <scratch dir>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
SCR="${2:?pass a scratch directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$SCR/$chip" "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors "$BUCKET/$p/wgcna-real/brca/" "$SCR/$chip/"
  for f in score.tsv host.txt times.txt sft.tsv; do
    cp "$SCR/$chip/$f" "$HERE/results/$chip/$f"
  done
  # every gene's module, kept once: the assignments are identical on all three chips (compare.txt)
  [ "$chip" = arm64 ] && cp "$SCR/$chip/modules.tsv" "$HERE/results/$chip/modules.tsv"
done
{
  for chip in x86_64 amd; do
    echo "== Graviton4 vs $chip"
    python3 -I "$HERE/compare.py" "$SCR/arm64" "$SCR/$chip"
  done
  echo "== Intel vs AMD (the same x86 image)"
  python3 -I "$HERE/compare.py" "$SCR/x86_64" "$SCR/amd"
  echo "== sha256 of the outputs"
  ( cd "$SCR" && shasum -a 256 */modules.tsv */eigengenes.tsv */sft.tsv )
} | tee "$HERE/results/compare.txt"
