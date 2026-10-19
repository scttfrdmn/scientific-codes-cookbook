#!/usr/bin/env bash
# Stage the bismark-real inputs:
#   ENCFF000LYO.fastq.gz   ENCODE RRBS, GM12878 replicate 1 (ENCSR000DFT, HudsonAlpha), 42,372,813
#                          single-end 36 bp reads, md5 11c5c6b097a490a4020cef6e16713bfe
#   GRCh38 no-alt          minimap2-real's 195-sequence analysis set (inputs/minimap2-real/), reused
#
# Tier: RODA. The FASTQ is copied server-side from the ENCODE bucket on AWS Open Data
# (s3://encode-public, us-west-2) into the bucket -- nothing passes through this machine -- and
# checked against ENCODE's md5 on a box (00-prep-trim, which also re-encodes and trims it). Then
# both aligners' indices are built once, on arm64, and shared by every chip. The sha256 of every staged object is in pins.sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/bismark-real"
SRC=s3://encode-public/2011/08/31/7b53c14d-89b5-43b7-9d98-9b35b7c58a42/ENCFF000LYO.fastq.gz

aws s3 ls --region "$REGION" "$BUCKET/inputs/minimap2-real/GRCh38_no_alt_analysis_set.fa" >/dev/null \
  || { echo "stage minimap2-real's GRCh38 first: measurements/minimap2-real/stage-inputs.sh" >&2; exit 1; }
curl -sSf -H 'Accept: application/json' https://www.encodeproject.org/files/ENCFF000LYO/ \
  | python3 -c 'import json,sys; f=json.load(sys.stdin); print("ENCODE:", f["accession"], f["md5sum"], f["read_count"], f["read_length"], f["run_type"], f["dataset"], f["status"])'
aws s3 ls --region "$REGION" "$P/ENCFF000LYO.fastq.gz" >/dev/null 2>&1 \
  || aws s3 cp --only-show-errors --region "$REGION" "$SRC" "$P/ENCFF000LYO.fastq.gz"
for f in prep-trim.sh prep-bismark.sh prep-bwameth.sh align-bismark.sh sort.sh align-bwameth.sh call.sh pins.sha256; do
  [ -e "$HERE/$f" ] && aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done

for t in trim bismark bwameth; do
  case $t in trim)    done_obj="$P/prep-trim/ENCFF000LYO.trimmed.fastq.gz" ;;
              bismark) done_obj="$P/Bisulfite_Genome/GA_conversion/BS_GA.rev.2.bt2" ;;
              bwameth) done_obj="$P/prep-bwameth/bwameth/ref.fa.bwameth.c2t.sa" ;; esac
  if ! aws s3 ls --region "$REGION" "$done_obj" >/dev/null 2>&1; then
    spec=$(python3 "$ROOT/scripts/spec_arch.py" bismark arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-prep-$t.task.json")
    spawn task run --spec "$spec" --region "$REGION" --wait
  fi
done
aws s3 cp --only-show-errors --region "$REGION" "$P/prep-trim/trim-checks.txt" -
aws s3 cp --only-show-errors --region "$REGION" "$P/prep/prep-checks.txt" -
aws s3 cp --only-show-errors --region "$REGION" "$P/prep-bwameth/prep-bwameth-checks.txt" -
echo "staged under $P/ -- add trimmed.sha256, fastq.sha256, index.sha256 and bwameth-index.sha256 to pins.sha256 on first staging"
