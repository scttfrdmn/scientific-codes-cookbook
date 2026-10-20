#!/usr/bin/env bash
# Stage measurements/cromwell-wdl-real.
#
#   sample      NA12878 chr20 at 36x (NYGC ERR3239334), the BAM recipes/gatk4 built -- shared, not copied
#   reference   GRCh38 chr20 from recipes/bwa-samtools and recipes/gatk4 -- shared, not copied
#   workflow    gatk-workflows/gatk4-germline-snps-indels 2.3.1, haplotypecaller-gvcf-gatk4.wdl,
#               unmodified; Broad's hg38 wgs_calling_regions (s3://broad-references, RODA's Broad
#               reference bucket); Docker's static 27.5.1 CLI for the Cromwell container. Fetched by
#               00-stage on a spawn host and held there to pins.sha256.
#   intervals   00-intervals: chr20's calling regions scattered 16 ways by GATK's IntervalListTools,
#               as Broad's production pipelines scatter them
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="s3://$B/inputs/cromwell-wdl-real"
for f in inputs/gatk4/NA12878.chr20.30x.bam inputs/gatk4/chr20.dict inputs/bwa-samtools/chr20.fa; do
  aws s3 ls --region "$REGION" "s3://$B/$f" > /dev/null || { echo "missing $f -- make stage RECIPE=gatk4 first" >&2; exit 1; }
done
for f in fetch.sh intervals.sh run.sh rss.py meta.py gvcf.py compare.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$P/$f"
done
launch() { spawn task run --wait --spec "$(python3 "$HERE/mkspec.py" arm64 "$B" "$(date +%s)" "$HERE/$1")"; }
aws s3 ls --region "$REGION" "$P/fetch.log" > /dev/null 2>&1 || launch 00-stage.task.json
aws s3 cp --region "$REGION" --only-show-errors "$P/fetch.log" - | grep -q 'FETCH OK'
aws s3 ls --region "$REGION" "$P/intervals/intervals.txt" > /dev/null 2>&1 || launch 00-intervals.task.json
aws s3 cp --region "$REGION" --only-show-errors "$P/intervals/intervals.txt" -
echo "staged to $P/"
