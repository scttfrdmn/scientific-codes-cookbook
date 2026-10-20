#!/usr/bin/env bash
# Copy each chip's small evidence files (not the QUAST output tarball) into results/<chip>/ and
# compare the deterministic QUAST outputs across chips.
#
#   bash collect.sh <bucket>
set -euo pipefail
B="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for c in arm64 x86_64 amd; do
  case $c in arm64) p=runs ;; x86_64) p=runs-x86_64 ;; amd) p=runs-amd ;; esac
  aws s3 cp --only-show-errors --recursive --exclude "quast-output.tgz" "$B/$p/quast-real/" "$HERE/results/$c/"
done
aws s3 cp --only-show-errors --recursive "$B/inputs/quast-real/prep/" "$HERE/results/prep/" \
  --exclude "*.fna.gz" --exclude "*.gff.gz"
{
  echo "== QUAST outputs (every .tsv/.txt but logs), sha256 per chip =="
  for c in arm64 x86_64 amd; do sed "s|^|$c  |" "$HERE/results/$c/fingerprints.txt"; done
  echo "== files whose bytes differ across chips =="
  cat "$HERE"/results/{arm64,x86_64,amd}/fingerprints.txt | sort -u | awk '{print $2}' | sort | uniq -d
  echo "== end =="
} > "$HERE/results/compare.txt"
cat "$HERE/results/compare.txt" | tail -20
