#!/usr/bin/env bash
# Pull the three chips' small evidence files into results/<chip>/ and write the cross-chip
# comparison to results/compare.txt. The 2.47M-row table itself stays in the bucket: real.py
# records its sha256 (of the uncompressed bytes) in metrics.txt, and that is what is compared.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in metrics.txt host.txt times.txt atoms_mismatch.tsv skeleton_mismatch.tsv; do
    aws s3 cp --only-show-errors "$BUCKET/$p/rdkit-real/chembl37/$f" "$HERE/results/$chip/$f"
  done
  aws s3 ls "$BUCKET/$p/rdkit-real/chembl37/rdkit_real.tsv.gz" | awk '{print "staged_gz_bytes", $3}' >> "$HERE/results/$chip/times.txt"
done
{
  echo "== metrics that differ between chips (every count, the version and the output sha256; wall excluded)"
  for chip in x86_64 amd; do
    echo "-- Graviton4 vs $chip"
    diff <(grep -v wall "$HERE/results/arm64/metrics.txt") <(grep -v wall "$HERE/results/$chip/metrics.txt") && echo "identical"
  done
  echo "== sha256 of the mismatch lists"
  ( cd "$HERE/results" && shasum -a 256 */atoms_mismatch.tsv */skeleton_mismatch.tsv )
  echo "== output_sha256 (uncompressed rdkit_real.tsv)"
  grep -H output_sha256 "$HERE"/results/*/metrics.txt | sed "s|$HERE/results/||"
} | tee "$HERE/results/compare.txt"
