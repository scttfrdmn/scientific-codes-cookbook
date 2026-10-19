#!/usr/bin/env bash
# Stage the spades-real inputs: one whole Illumina MiSeq 2x300 run of wild-type E. coli K-12 MG1655
# (DRR171213, "Ecoli_MG1655_NN", PRJDB8030: 760,683 pairs, 450,083,225 bases, ~97x), and its closed
# reference, NCBI RefSeq GCF_000005845.2 (U00096.3, 4,641,652 bp).
#
# Tier: RODA. The run is copied server-side from the AWS Open Data SRA mirror (s3://sra-pub-run-odp,
# us-east-1) into the bucket -- nothing passes through this machine -- and converted to FASTQ once on
# a box (00-prep, sra-tools), which ties the dump back to ENA's own read_count and base_count and
# fetches the reference, checked against NCBI's own md5. Every staged object's sha256 is in
# pins.sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/spades-real"
ACC=DRR171213

curl -sSf "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=$ACC&result=read_run&fields=sample_title,library_strategy,library_layout,read_count,base_count&format=tsv" \
  | awk -F'\t' 'NR==2{print "ENA: " $0; if ($3!="WGS" || $4!="PAIRED") exit 1}' || { echo "$ACC is not paired WGS" >&2; exit 1; }
if ! aws s3 ls --region "$REGION" "$P/$ACC.sra" >/dev/null 2>&1; then
  aws s3 cp --only-show-errors --source-region us-east-1 --region "$REGION" \
    "s3://sra-pub-run-odp/sra/$ACC/$ACC" "$P/$ACC.sra"
fi
for f in prep.sh assemble.sh evaluate.sh pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done

if ! aws s3 ls --region "$REGION" "$P/prep/${ACC}_2.fastq.gz" >/dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" spades arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-prep.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
aws s3 cp --only-show-errors --region "$REGION" "$P/prep/prep-checks.txt" -
echo "staged under $P/prep/ -- add the .sra, fastq, reference and pairs.txt sha256 to pins.sha256 on first staging"
