#!/usr/bin/env bash
# Stage measurements/qc-aggregate-real: upload the scripts and pins. The eleven FASTQs are NOT
# fetched here -- they are already in the bucket from the bwa, salmon, metaphlan and mageck real
# workloads (see each one's stage-inputs.sh), and the specs read them in place (cohort.tsv). The
# FastQC task checks every staged byte against inputs.sha256 and five against ENA's own md5.
#
# Usage: stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
D="$(cd "$(dirname "$0")" && pwd)"
for f in probe.sh fastqc.sh multiqc.sh recount.pl check_fastqc.pl check_multiqc.py cohort.tsv ena.md5 inputs.sha256; do
  [ -f "$D/$f" ] && aws s3 cp "$D/$f" "s3://$B/inputs/qc-aggregate-real/$f" --only-show-errors
done
while IFS=$'\t' read -r f key _; do
  [ "$f" = file ] && continue
  aws s3 ls "s3://$B/$key" > /dev/null || { echo "missing: s3://$B/$key -- stage it with its owning measurement"; exit 1; }
done < "$D/cohort.tsv"
aws s3 ls "s3://$B/inputs/qc-aggregate-real/"
