#!/usr/bin/env bash
# Stage abundance-real. Nothing new is downloaded:
#   * the two kraken2 reports are kraken2-real's whole-run classifications of the ZymoBIOMICS
#     D6300 (ERR2984773) and D6310 (ERR2935805) runs, byte-identical on all three chips
#     (measurements/kraken2-real/results/compare.txt); copied S3-to-S3 under this prefix so a
#     re-run of kraken2-real cannot change what this measurement reads;
#   * the read-length distributions are taken out of the recipe's pinned standard-8 tarball, on
#     AWS, by 00-extract.task.json (run once, on Graviton4: bioconda's x86 image has no tar);
#   * zymo.tsv is kraken2-real's, read from its prefix.
# Every object the task reads is pinned in pins.sha256 and verified on the box before Bracken runs.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/abundance-real"

aws s3 ls --region "$REGION" "$P/k2_standard_08gb_20250402/database150mers.kmer_distrib" >/dev/null \
  || { echo "extract the distributions first: spawn task run --wait --spec <00-extract resolved by scripts/spec_arch.py>" >&2; exit 1; }
aws s3 ls --region "$REGION" "$BUCKET/inputs/kraken2-real/zymo.tsv" >/dev/null \
  || { echo "stage kraken2-real first: bash measurements/kraken2-real/stage-inputs.sh <bucket>" >&2; exit 1; }
for acc in ERR2984773 ERR2935805; do
  aws s3 ls --region "$REGION" "$P/$acc.report.txt" >/dev/null 2>&1 \
    || aws s3 cp --only-show-errors --region "$REGION" \
         "$BUCKET/runs/kraken2-real/zymo/$acc/report.txt" "$P/$acc.report.txt"
done
for f in run.py score.py pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
echo "staged under $P/"
