#!/usr/bin/env bash
# Stage the metaphlan-real inputs: the two whole Illumina shotgun runs of the ZymoBIOMICS standards
# from Nicholls et al. 2019 (GigaScience, PRJEB29504) --
#   ERR2984773  D6300 "even"  MiSeq 2x151,      8,785,731 pairs, 2.65 Gbp
#   ERR2935805  D6310 "log"   HiSeq 1500 2x101, 47,832,553 pairs, 9.66 Gbp
#
# Tier: RODA. Each run is copied server-side from the AWS Open Data SRA mirror
# (s3://sra-pub-run-odp, us-east-1) into the bucket -- nothing passes through this machine -- and
# converted to FASTQ once on a box (00-prep, sra-tools), which ties each dump back to ENA's own
# read_count and base_count. The sha256 of every staged object is in pins.sha256. The marker
# database and bowtie2 index are the recipe's own (recipes/metaphlan/stage-inputs.sh), shared.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/metaphlan-real"

aws s3 ls --region "$REGION" "$BUCKET/inputs/metaphlan/mpa_vJun23_CHOCOPhlAnSGB_202403_bt2.tar" >/dev/null \
  || { echo "stage the recipe first: make stage RECIPE=metaphlan" >&2; exit 1; }

for ACC in ERR2984773 ERR2935805; do
  # shotgun, not amplicon (the recipe's trap); prep asserts the counts printed here
  curl -sSf "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=$ACC&result=read_run&fields=sample_title,library_strategy,read_count,base_count&format=tsv" \
    | awk -F'\t' 'NR==2{print "ENA: " $0; if ($3!="WGS") exit 1}' || { echo "$ACC is not WGS" >&2; exit 1; }
  if ! aws s3 ls --region "$REGION" "$P/$ACC.sra" >/dev/null 2>&1; then
    aws s3 cp --only-show-errors --source-region us-east-1 --region "$REGION" \
      "s3://sra-pub-run-odp/sra/$ACC/$ACC" "$P/$ACC.sra"
  fi
done
for f in prep.sh profile.sh score.py zymo_d6310.tsv pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done

if ! aws s3 ls --region "$REGION" "$P/prep/ERR2935805/ERR2935805_2.fastq.gz" >/dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" metaphlan arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-prep.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
for ACC in ERR2984773 ERR2935805; do
  aws s3 cp --only-show-errors --region "$REGION" "$P/prep/$ACC/prep-checks.txt" -
done
echo "staged under $P/prep/ -- add the fastq and reads_ge70 sha256 to pins.sha256 on first staging"
