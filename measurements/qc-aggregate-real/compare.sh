#!/usr/bin/env bash
# Cross-chip comparison of every text output the two tasks wrote: sha256 per file per chip.
# Small text only (fastqc_data.txt, summary.txt, recount, the MultiQC tables); the zips differ as
# archives (member timestamps), so they are not compared.
#   compare.sh <bucket> <nonce>
set -euo pipefail
B="${1:?bucket}"; N="${2:?nonce}"
for a in runs runs-x86_64 runs-amd; do
  for t in fastqc multiqc; do
    aws s3 ls "s3://$B/$a/qc-aggregate-real/$N/$t/" | awk '{print $4}' \
      | grep -E '(fastqc_data|summary)\.txt$|^recount\.tsv$|^fastqc_values\.tsv$|^multiqc_(fastqc|general_stats)\.txt$' \
      | while read -r f; do
          printf '%s\t%s/%s\t%s\n' "$a" "$t" "$f" "$(aws s3 cp "s3://$B/$a/qc-aggregate-real/$N/$t/$f" - | shasum -a 256 | cut -c1-16)"
        done
  done
done | awk -F'\t' '{h[$2,$1]=$3; f[$2]=1} END{
  for (k in f) { a=h[k,"runs"]; x=h[k,"runs-x86_64"]; m=h[k,"runs-amd"]
    printf "%-44s %s  intel:%s  amd:%s\n", k, a, (x==a?"same":"DIFF"), (m==a?"same":"DIFF") } }' | sort
