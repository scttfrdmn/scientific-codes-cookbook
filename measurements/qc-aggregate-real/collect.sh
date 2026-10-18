#!/usr/bin/env bash
# Pull one chip's small text results (no zips, no FASTQ) into results/<arch>/, with spawn's
# completion records, for the write-up.
#   collect.sh <bucket> <nonce> <arm64|x86_64|amd>
set -euo pipefail
B="${1:?bucket}"; N="${2:?nonce}"; A="${3:?arch}"
D="$(cd "$(dirname "$0")" && pwd)/results/$A"
case "$A" in arm64) P=runs; S="" ;; x86_64) P=runs-x86_64; S="-x8664" ;; amd) P=runs-amd; S="-amd" ;; esac
mkdir -p "$D"
for t in fastqc multiqc; do
  for f in host.txt smoke-check-fastqc.txt report-times.txt inputs-check.txt recount.tsv fastqc_values.tsv \
           smoke-check.txt partial.txt multiqc_fastqc.txt multiqc_general_stats.txt; do
    aws s3 cp "s3://$B/$P/qc-aggregate-real/$N/$t/$f" "$D/$t.$f" --only-show-errors 2>/dev/null || true
  done
  R=$(echo "$B" | sed 's/^cookbook-/spawn-results-/')
  aws s3 cp "s3://$R/tasks/cookbook-qc-aggregate-real-$t$S-$N/completion.json" "$D/$t.completion.json" --only-show-errors
done
ls "$D"
