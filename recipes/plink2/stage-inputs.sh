#!/usr/bin/env bash
# Stage the binary fileset that BOTH tools analyse, by running PLINK 1.9 once.
#
# WHY A TASK RATHER THAN A LOCAL FETCH. There is nothing to download: the fileset is generated
# by `plink --dummy --seed 42`. But it must be generated EXACTLY ONCE and staged, because the
# whole claim of this recipe is that PLINK 2 and PLINK 1.9 agree on identical BYTES. Letting
# each image regenerate from the same seed would be a second thing to keep true -- the
# cross-check rule says point the second tool at the first one's staged input.
#
# This task also computes PLINK 1.9's side of the comparison (allele counts, missingness, HWE)
# and stages those summaries, so the PLINK 2 task reads 1.9's recorded answers rather than
# re-deriving them.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=plink2 does this}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if aws s3 ls "$BUCKET/inputs/plink2/data.bed" --region "$REGION" >/dev/null 2>&1 \
   && aws s3 ls "$BUCKET/inputs/plink2/p19.hwe.tsv" --region "$REGION" >/dev/null 2>&1; then
  echo "fileset and PLINK 1.9 summaries already staged"
  exit 0
fi

echo "== PLINK 1.9 generates the fileset and its own summaries (~1 min, self-terminating) =="
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
sed "s|\${COOKBOOK_BUCKET}|${BUCKET#s3://}|g" "$HERE/01-generate-19.task.json" > "$tmp/gen.json"
spawn task run --spec "$tmp/gen.json" --region "$REGION" --wait

echo "done."
echo "  $BUCKET/inputs/plink2/data.{bed,bim,fam}  (300 samples x 1000 variants, 2% missing)"
echo "  $BUCKET/inputs/plink2/data.sha256         (verified by the PLINK 2 task before it runs)"
echo "  $BUCKET/inputs/plink2/p19.{minor,miss,hwe}.tsv  (PLINK 1.9's answers)"
