#!/usr/bin/env bash
# Pull each chip's small evidence files into results/<chip>/<task>/ and write results/compare.txt.
# The VCFs, the panel and the logs over a few MB stay in the bucket.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
R="$HERE/results"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  for t in prep beagle mm4; do
    aws s3 cp --recursive --only-show-errors --exclude '*' \
      --include '*.tsv' --include '*.log' --include 'beagle.stdout' --include '*.samples' \
      --exclude 'ref.sites.tsv' --exclude 'typed.pos' \
      "$BUCKET/$p/imputation-real/$t/" "$R/$chip/$t/"
  done
done
v(){ awk -F'\t' -v k="$2" '$1==k{print $2}' "$1"; }
{
  echo "value                  Graviton4 vs Intel   Graviton4 vs AMD"
  for kv in prep/results.tsv:ref_body_md5 prep/results.tsv:truth_body_md5 prep/results.tsv:target_body_md5 \
            prep/results.tsv:ref_msav_md5 beagle/results.tsv:beagle_body_md5 mm4/results.tsv:mm4_body_md5 \
            mm4/results.tsv:scores_md5; do
    f=${kv%%:*}; k=${kv#*:}
    a=$(v "$R/arm64/$f" "$k"); i=$(v "$R/x86_64/$f" "$k"); m=$(v "$R/amd/$f" "$k")
    printf '%-22s %-20s %s\n' "$k" "$([ "$a" = "$i" ] && echo identical || echo DIFFER)" "$([ "$a" = "$m" ] && echo identical || echo DIFFER)"
  done
  echo
  echo "== scores.tsv, every row: arm64 / x86_64 / amd"
  paste "$R/arm64/mm4/scores.tsv" "$R/x86_64/mm4/scores.tsv" "$R/amd/mm4/scores.tsv" \
    | awk -F'\t' '{d = ($4==$8 && $4==$12) ? "" : "\tDIFFER"; print $1 "\t" $2 "\t" $3 "\t" $4 "\t" $8 "\t" $12 d}'
} | tee "$R/compare.txt"
