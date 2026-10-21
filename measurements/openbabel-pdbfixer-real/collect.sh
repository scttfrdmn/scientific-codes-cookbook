#!/usr/bin/env bash
# Pull each chip's small evidence files into results/<chip>/{ob,fix}/ and write the cross-chip
# comparison to results/compare.txt. The 2.9M-row ob_real.tsv stays in the bucket: ob_real.py records
# the sha256 of its uncompressed bytes in metrics.txt, and that is what is compared.
#
#   bash collect.sh <bucket> <obabel nonce> <pdbfixer nonce>     (the nonces of the verifying runs)
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
NOB="${2:?obabel nonce}" NFIX="${3:?pdbfixer nonce}"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; s="" ;; x86_64) p=runs-x86_64; s="-x8664" ;; amd) p=runs-amd; s="-amd" ;; esac
  mkdir -p "$HERE/results/$chip/ob" "$HERE/results/$chip/fix"
  aws s3 cp --only-show-errors --recursive --exclude '*.gz' --exclude 'ob_real.log' \
    "$BUCKET/$p/openbabel-pdbfixer-real/chembl37/" "$HERE/results/$chip/ob/"
  aws s3 cp --only-show-errors --recursive --exclude 'entries.partial.tsv' \
    "$BUCKET/$p/openbabel-pdbfixer-real/pdb-ab/" "$HERE/results/$chip/fix/"
  spawn task status "cookbook-openbabel-real-chembl37$s-$NOB" -o json > "$HERE/results/$chip/ob/completion.json"
  spawn task status "cookbook-pdbfixer-real-ab$s-$NFIX" -o json > "$HERE/results/$chip/fix/completion.json"
done
python3 -I "$HERE/compare.py" "$HERE/results" | tee "$HERE/results/compare.txt"
