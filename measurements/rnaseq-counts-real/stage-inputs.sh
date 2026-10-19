#!/usr/bin/env bash
# Stage rnaseq-counts-real. No new data: the reads, the annotation and the STAR index are the ones
# salmon-real and star-real already staged, so the counters run on the bytes those measurements
# did.
#
# DATA TIER: durable public ids, then build.
#   ERR188026 (ENA, Geuvadis GEUV:NA20510, 15,800,127 pairs), pinned by ENA's fastq_md5.
#   Ensembl release-116 GRCh38 GTF, pinned by Ensembl's CHECKSUMS; release paths are immutable.
#   inputs/star-index-GRCh38-116/, published by measurements/star-real (build-publish.sh).
#   The alignment (task 0, build tier) is made once on Graviton4 by 00-star-align.task.json and
#   pinned by sha256 in inputs.sha256, which every counting task checks before it runs.
#
# The scripts are staged as pinned files rather than inlined in the spec (EC2 user data is
# 16 KiB, spawn#1063); each spec checks their sha256 before running them.
#   stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
DST="$BUCKET/inputs/rnaseq-counts-real"
HERE="$(cd "$(dirname "$0")" && pwd)"
for f in align.sh featurecounts.sh htseq.sh compare.py; do
  aws s3 cp --only-show-errors "$HERE/$f" "$DST/$f"
  shasum -a 256 "$HERE/$f"
done
# after task 0: the alignment's pins, as the counting tasks stage the files
if [ -f "$HERE/inputs.sha256" ]; then
  aws s3 cp --only-show-errors "$HERE/inputs.sha256" "$DST/inputs.sha256"
  cat "$HERE/inputs.sha256"
fi
