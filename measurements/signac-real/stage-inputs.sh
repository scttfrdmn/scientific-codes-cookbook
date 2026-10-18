#!/usr/bin/env bash
# Stage measurements/signac-real: the 10x Genomics 10k_pbmc_ATACv2_nextgem_Chromium_Controller run
# (Cell Ranger ATAC 2.1.0: fragments + index, peaks, per-barcode metrics) and the Ensembl 98 GTF
# (the gene build under Cell Ranger's GRCh38-2020-A), plus the independent recount every Signac
# assertion is compared with.
#
# Nothing is downloaded here. This script uploads the scripts and pins, then launches
# 01-reference.task.json, which fetches on AWS, checks pins.sha256 (and Ensembl's own CHECKSUMS
# for the GTF), derives cells/annotation/gene windows and runs the awk recount (reference.sh).
#
# Usage: stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
D="$(cd "$(dirname "$0")" && pwd)"
for f in reference.sh bins.awk overlap.awk windows.awk gtf.awk pins.sha256 signac-real.R; do
  aws s3 cp "$D/$f" "s3://$B/inputs/signac-real/$f" --only-show-errors
done
sed "s|\${COOKBOOK_BUCKET}|$B|g" "$D/01-reference.task.json" > "${TMPDIR:-/tmp}/signac-real-reference.json"
spawn task run --spec "${TMPDIR:-/tmp}/signac-real-reference.json" --wait
aws s3 ls "s3://$B/inputs/signac-real/" --recursive
