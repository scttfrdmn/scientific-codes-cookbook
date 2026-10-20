#!/usr/bin/env bash
# Stage the sourmash-real inputs.
#
# Reads: the two whole ZymoBIOMICS shotgun runs metaphlan-real and kraken2-real use (PRJEB29504,
# RODA -> FASTQ.gz by measurements/metaphlan-real/stage-inputs.sh), reused byte for byte.
# Database: sourmash's prepared GTDB R10-RS226 species-representative sketches (k=31, scaled=1000)
# and their lineages, fetched on AWS by 00-fetch (fetch.py) into the bucket -- nothing passes through
# this machine -- and pinned by sha256 in pins.sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/sourmash-real"

aws s3 ls --region "$REGION" "$BUCKET/inputs/metaphlan-real/prep/ERR2935805/ERR2935805_2.fastq.gz" >/dev/null \
  || { echo "stage the reads first: bash measurements/metaphlan-real/stage-inputs.sh $1" >&2; exit 1; }
for f in fetch.py run.py score.py zymo.tsv pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done
if ! aws s3 ls --region "$REGION" "$P/gtdb-rs226/gtdb-rs226-reps.k31.sig.zip" >/dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" sourmash arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-fetch.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
fi
aws s3 cp --only-show-errors --region "$REGION" "$P/gtdb-rs226/db.sha256" -
echo "check those against pins.sha256; a different sha256 is a different database"
