#!/usr/bin/env bash
# Stage the bwa-mem2-real inputs.
#
#   reads    HG00096's three low-coverage paired runs (every one sequence.index lists for the
#            sample). SRR062634 is already in the bucket from measurements/bwa-real; SRR062635 and
#            SRR062641 are copied server-side from RODA (s3://1000genomes). Pinned by the md5s
#            1000 Genomes publishes in sequence.index (sample.tsv); every task checks them.
#   index    GRCh38_full_analysis_set_plus_decoy_hla: bwa's (published, measurements/bwa-real) and
#            bwa-mem2's (built by recipes/bwa-mem2 00-index). Every task records their sha256 and
#            collect.sh requires all three chips to have read the same bytes.
#   scripts  align.sh, lib.sh, sample.tsv -- pinned by sha256 in each spec's command
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/bwa-mem2-real"
R=GRCh38_full_analysis_set_plus_decoy_hla.fa

for f in "inputs/bwa-real/$R.bwt" "inputs/bwa-real/$R.alt" inputs/bwa-real/SRR062634_1.filt.fastq.gz \
         inputs/bwa-mem2/mem2-index.tar; do
  aws s3 ls --region "$REGION" "$BUCKET/$f" > /dev/null \
    || { echo "missing $BUCKET/$f -- stage measurements/bwa-real and recipes/bwa-mem2 first" >&2; exit 1; }
done
for run in SRR062635 SRR062641; do
  for m in 1 2; do
    f=${run}_$m.filt.fastq.gz
    aws s3 ls --region "$REGION" "$P/reads/$f" > /dev/null 2>&1 \
      || aws s3 cp --only-show-errors --source-region us-east-1 --region "$REGION" \
           "s3://1000genomes/phase3/data/HG00096/sequence_read/$f" "$P/reads/$f"
  done
done
for f in align.sh lib.sh sample.tsv; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
echo "staged $P/"
