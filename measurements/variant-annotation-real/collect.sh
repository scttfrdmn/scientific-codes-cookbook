#!/usr/bin/env bash
# Pull each chip's small evidence files into results/<chip>/<task>/ and write results/compare.txt.
# The annotated VCFs, the pair tables and the large logs (VEP's warnings, ~35 MB) stay in the bucket.
#
#   bash collect.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
R="$HERE/results"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs ;; *) p=runs-$chip ;; esac
  for t in csq snpeff vep; do
    aws s3 cp --recursive --only-show-errors --exclude '*.vcf.gz' --exclude '*.pairs.tsv' --exclude '*_warnings.txt' \
      --exclude 'disagree.tsv' --exclude 'pep_mismatch.tsv' --exclude '*.html' --exclude 'build*.log' --exclude 'snpeff.stats.csv' \
      --exclude 'vep.log' --exclude 'ann.log' --exclude '*.genes.txt' \
      "$BUCKET/$p/variant-annotation-real/$t/" "$R/$chip/$t/"
  done
done
v(){ awk -F'\t' -v k="$2" '$1==k{print $2}' "$1"; }
{
  echo "value                         Graviton4 vs Intel   Graviton4 vs AMD"
  for kv in csq/results.tsv:sites_text_md5 csq/results.tsv:csq_text_md5 csq/results.tsv:csq_pairs_md5 \
            snpeff/results.tsv:snpeff_text_md5 snpeff/results.tsv:snpeff_pairs_md5 \
            vep/results.tsv:vep_text_md5 vep/results.tsv:vep_pairs_md5 vep/compare.tsv:all_three_disagree_x5; do
    f=${kv%%:*}; k=${kv#*:}
    a=$(v "$R/arm64/$f" "$k"); i=$(v "$R/x86_64/$f" "$k"); m=$(v "$R/amd/$f" "$k")
    printf '%-30s %-20s %s\n' "$k" "$([ "$a" = "$i" ] && echo identical || echo "DIFFER")" "$([ "$a" = "$m" ] && echo identical || echo "DIFFER")"
  done
  echo
  echo "== compare.tsv, every key: arm64 / x86_64 / amd"
  paste "$R/arm64/vep/compare.tsv" "$R/x86_64/vep/compare.tsv" "$R/amd/vep/compare.tsv" | awk -F'\t' '{print $1 "\t" $2 "\t" $4 "\t" $6 ($2==$4 && $2==$6 ? "" : "\tDIFFER")}'
} | tee "$R/compare.txt"
