#!/usr/bin/env bash
# Build kallisto's index. Everything else this recipe needs -- the Ensembl 116 cDNA, the reads, and
# salmon's own quant table for the cross-check -- is staged by `make stage RECIPE=salmon` and read
# from there, because the comparison only means something on identical bytes.
#
# The index build runs as a spawn TASK: it is the memory-bound step (r8g) and takes ~8 min, which is
# not something to do over a home uplink. Building a pinned artifact is staging; it just runs on the box.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
REGION="${AWS_REGION:-us-west-2}"
SPEC="$(dirname "$0")/01-index.task.json"
DST="s3://$BUCKET/inputs/kallisto/kallisto.idx"

if ! aws s3 ls "s3://$BUCKET/inputs/salmon-real/ensembl116_cdna.fa.gz" --region "$REGION" >/dev/null 2>&1; then
  echo "run 'make stage RECIPE=salmon' first -- this recipe reads its cDNA and reads" >&2
  exit 1
fi

if aws s3 ls "$DST" --region "$REGION" >/dev/null 2>&1; then
  echo "kallisto index already staged: $DST"
else
  echo "building the kallisto index on r8g (~8 min, ~\$0.12, self-terminating)..."
  tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
  sed "s|\${COOKBOOK_BUCKET}|$BUCKET|g" "$SPEC" > "$tmp"
  spawn task run --spec "$tmp" --region "$REGION" --wait
fi

echo "staged $DST   (0.87 GiB; salmon's equivalent is 1.6 GiB)"
echo "the cross-check also reads runs/salmon/r1/quant-c8g-16t.sf -- run 'make run RECIPE=salmon' first."
