#!/usr/bin/env bash
# Stage rsem-real. No new data: the reads (ERR188026) and the transcriptome (Ensembl 116 cDNA) are
# the bytes salmon-real already staged, and salmon-real's own Graviton4 quant.sf is copied
# server-side as the cross-check's other half.
#
# DATA TIER: durable public ids, then build.
#   ERR188026 (ENA, Geuvadis GEUV:NA20510, 15,800,127 pairs), pinned by ENA's fastq_md5.
#   Ensembl release-116 Homo_sapiens.GRCh38.cdna.all.fa.gz, pinned by Ensembl's CHECKSUMS; release
#   paths are immutable.
#   The RSEM reference and the bowtie2 alignment (build tier) are made once on Graviton4 by tasks
#   00 and 01 and pinned by sha256 in inputs.sha256, which every quant task checks first.
#
# The scripts are staged as pinned files rather than inlined in the spec (EC2 user data is
# 16 KiB, spawn#1063); each spec checks their sha256 before running them.
#   stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
DST="$BUCKET/inputs/rsem-real"
HERE="$(cd "$(dirname "$0")" && pwd)"
for f in prepare.sh align.sh quant.sh check.R crosschip.R timed.py; do
  aws s3 cp --only-show-errors "$HERE/$f" "$DST/$f"
  shasum -a 256 "$HERE/$f"
done
aws s3 cp --only-show-errors "$BUCKET/runs/salmon/r1/quant-c8g-16t.sf" "$DST/salmon.quant.sf"
# after task 01: every input the quant tasks stage, by sha256
if [ -f "$HERE/inputs.sha256" ]; then
  aws s3 cp --only-show-errors "$HERE/inputs.sha256" "$DST/inputs.sha256"
  cat "$HERE/inputs.sha256"
fi
