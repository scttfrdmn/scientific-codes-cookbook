#!/usr/bin/env bash
# Stage transcript-assembly-real. No new data: the reads (ERR188026), the genome and the annotation
# are the ones salmon-real and star-real already staged, so StringTie runs on bytes other
# measurements already pinned.
#
# DATA TIER: durable public ids, then build.
#   ERR188026 (ENA, Geuvadis GEUV:NA20510, 15,800,127 pairs), pinned by ENA's fastq_md5.
#   Ensembl release-116 GRCh38 primary assembly and GTF, pinned by Ensembl's CHECKSUMS; release
#   paths are immutable.
#   The HISAT2 index and the sorted alignment (build tier) are made once on Graviton4 by tasks 00
#   and 01 and pinned by sha256 in inputs.sha256, which every StringTie task checks first.
#
# The scripts are staged as pinned files rather than inlined in the spec (EC2 user data is
# 16 KiB, spawn#1063); each spec checks their sha256 before running them.
#   stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
DST="$BUCKET/inputs/transcript-assembly-real"
HERE="$(cd "$(dirname "$0")" && pwd)"
for f in align.sh sort.sh stringtie.sh gffcompare.sh timed.py check_st.py ref.sha256; do
  aws s3 cp --only-show-errors "$HERE/$f" "$DST/$f"
  shasum -a 256 "$HERE/$f"
done
# after task 01: the alignment's pins, as the StringTie tasks stage the files
if [ -f "$HERE/inputs.sha256" ]; then
  aws s3 cp --only-show-errors "$HERE/inputs.sha256" "$DST/inputs.sha256"
  cat "$HERE/inputs.sha256"
fi
