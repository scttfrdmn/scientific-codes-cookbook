#!/usr/bin/env bash
# Stage measurements/minimap2-real. Nothing is downloaded here: every byte moves S3-to-S3 or is
# built inside a spawn task on AWS.
#
# Reused, not re-staged (one copy of the bytes, each verified by sha256 in-task):
#   inputs/minimap2/hifi.chr20.fq.gz            the recipe's 217,281 NA12878 HiFi reads (GIAB deposit)
#   inputs/bwa-real/GRCh38_full_analysis_set_plus_decoy_hla.fa(.fai)   RODA 1000genomes, GRCh38
#   inputs/gatk4/truth.chr20.{vcf.gz,bed}       GIAB HG001 NISTv4.2.1, chr20
# Built by prep.task.json (tier: build, from the pinned bytes above, deterministic):
#   GRCh38_no_alt_analysis_set.fa(.fai)         the 195 non-alt/HLA/decoy sequences of that FASTA
#   pbmm2.chr20.tsv                             the depositors' placement of each read
set -euo pipefail

BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
I="s3://$BUCKET/inputs/minimap2-real"

for need in inputs/minimap2/hifi.chr20.fq.gz inputs/bwa-real/GRCh38_full_analysis_set_plus_decoy_hla.fa \
            inputs/gatk4/truth.chr20.vcf.gz; do
  aws s3 ls "s3://$BUCKET/$need" --region "$REGION" > /dev/null || {
    echo "missing s3://$BUCKET/$need -- stage recipes/minimap2, measurements/bwa-real and recipes/gatk4 first"; exit 1; }
done

for f in prep.sh prep-genome.sh prep-chunk.sh; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$I/$f"
done
if ! aws s3 ls "$I/pbmm2.chr20.tsv" --region "$REGION" > /dev/null 2>&1; then
  echo "building the no-alt reference and the chr20 pbmm2 table on m8g.2xlarge (~2 min)..."
  spawn task run --spec "$(python3 "$ROOT/scripts/spec_arch.py" minimap2 arm64 "$BUCKET" "$(date +%s)" "$HERE/prep.task.json")" \
    --region "$REGION" --wait
fi
if ! aws s3 ls "$I/wg-pins.sha256" --region "$REGION" > /dev/null 2>&1; then
  echo "cutting the whole GIAB HiFi deposit into 25 FASTQ chunks on r8g.8xlarge (~23 min, ~\$0.73)..."
  spawn task run --spec "$(python3 "$ROOT/scripts/spec_arch.py" minimap2 arm64 "$BUCKET" "$(date +%s)" "$HERE/prep-genome.task.json")" \
    --region "$REGION" --wait
fi
# The chunks are deterministic (same records, same image, same gzip -1), so a rebuild must
# reproduce the committed hashes.
aws s3 cp --only-show-errors --region "$REGION" "$I/wg-pins.sha256" - | diff - "$HERE/wg-pins.sha256"

# pins.sha256 is committed: the prep outputs' hashes plus the two workload scripts'. Check the
# scripts locally (the pinned files that live in this repo); the tasks check everything else.
grep -E ' (align|genome)\.sh$' "$HERE/pins.sha256" | (cd "$HERE" && shasum -a 256 -c -)
for f in align.sh genome.sh pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$I/$f"
done
echo "staged to $I/"
