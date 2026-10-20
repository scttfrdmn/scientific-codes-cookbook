#!/usr/bin/env bash
# Stage the quast-real inputs.
#
# Tier: durable public id. Nine published complete-genome assemblies of E. coli K-12 MG1655
# (NCBI GenBank, versioned accessions in assemblies.tsv) and the reference annotation
# (GCF_000005845.2 GFF) are fetched once on a box by 00-prep, checked against NCBI's own md5s and
# assembly-report lengths, and pinned by sha256 in pins.sha256. Reused, not re-staged: the
# reference U00096.3 (inputs/spades-real/prep/) and spades-real's assembly of DRR171213 (byte-identical
# on all three chips). Nothing passes through this machine but the scripts.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/quast-real"
for f in prep.sh run.sh mkcontrols.py check.py assemblies.tsv spades-real.report.tsv pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
if ! aws s3 ls --region "$REGION" "$P/prep/staged.sha256" >/dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" quast arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-prep.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
aws s3 cp --only-show-errors --region "$REGION" "$P/prep/prep-checks.txt" -
echo "staged under $P/prep/ -- on first staging, add prep/staged.sha256 to pins.sha256"
