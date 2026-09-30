#!/usr/bin/env bash
# Stage what bwa-mem2 needs that bwa does not: the reference FASTA itself, and an index built
# from it.
#
# bwa-samtools never stages the 3.0 GB `.fa` — `bwa mem` reads only .amb/.ann/.bwt/.pac/.sa, so
# skipping it cuts staging from 12 GiB to 8.9. bwa-mem2 has to have the FASTA, because its index
# is a different format and must be built locally; bioconda ships no prebuilt one.
#
# The index build runs as a spawn TASK, not here: it needs ~14 min on 16 cores and produces
# 16.5 GiB, which is not something to push over a home uplink. Building it is staging, so it
# belongs in this script — it just executes on the box.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
REGION="${AWS_REGION:-us-west-2}"
R=GRCh38_full_analysis_set_plus_decoy_hla.fa
SRC="s3://1000genomes/technical/reference/GRCh38_reference_genome/$R"
DST="s3://$BUCKET/inputs/bwa-real/$R"
SPEC="$(dirname "$0")/00-index.task.json"

# 1. the reference, server-side: no download, no egress, idempotent.
if aws s3 ls "$DST" --region "$REGION" >/dev/null 2>&1; then
  echo "reference already staged: $DST"
else
  echo "copying the reference server-side (3.0 GB, no local download)..."
  aws s3 cp "$SRC" "$DST" --region "$REGION" --only-show-errors
fi

# 2. the bwa-mem2 index, built on the box and published as one flat tar.
#    A directory output cannot work on the container path (output parents are never mkdir-ed, so
#    dockerd creates them as root), so it travels as mem2-index.tar and the consumer untars it.
if aws s3 ls "s3://$BUCKET/inputs/bwa-mem2/mem2-index.tar" --region "$REGION" >/dev/null 2>&1; then
  echo "bwa-mem2 index already staged: s3://$BUCKET/inputs/bwa-mem2/mem2-index.tar"
else
  echo "building the bwa-mem2 index on r8g.4xlarge (~14 min, ~\$0.23, self-terminating)..."
  tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
  sed "s|\${COOKBOOK_BUCKET}|$BUCKET|g" "$SPEC" > "$tmp"
  spawn task run --spec "$tmp" --region "$REGION" --wait
fi

echo "staged:"
echo "  $DST"
echo "  s3://$BUCKET/inputs/bwa-mem2/mem2-index.tar   (16.5 GiB, 3.14x bwa's index)"
echo "reads and the bwa index come from 'make stage RECIPE=bwa-samtools'."
