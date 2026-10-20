#!/usr/bin/env bash
# Stage the mash-real inputs.
#
# Tier: durable public ids. GTDB release R232 (Apr 2026) defines the genus and its species: the
# bacterial metadata and sp_clusters_r232.tsv, checked against GTDB's own MD5SUM.txt. The genomes are
# the NCBI assemblies GTDB names, by versioned accession, each checked against NCBI's own
# md5checksums.txt. 00-prep fetches all of it once on a box and writes one flat tar; the tar and both
# tables are then pinned by sha256 in pins.sha256. Nothing passes through this machine but scripts.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/mash-real"
for f in prep.sh fetch1.sh run.sh sourmash_xcheck.py pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
if ! aws s3 ls --region "$REGION" "$P/prep/staged.sha256" >/dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" mash arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-prep.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
aws s3 cp --only-show-errors --region "$REGION" "$P/prep/prep-checks.txt" -
echo "staged under $P/prep/ -- on first staging, add prep/staged.sha256 to pins.sha256"
