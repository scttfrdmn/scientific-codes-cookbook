#!/usr/bin/env bash
# Stage methylation-array-real: the whole GSE42861 cohort (689 Illumina 450k arrays, GEO), fetched
# ON AWS by fetch.task.json -- never to this machine -- plus the three task scripts.
#
# Tier: a durable public id (GEO accession GSE42861). GEO publishes no checksum for
# GSE42861_RAW.tar, so the pin is the sha256 the first fetch recorded (pins.sha256); fetch.sh checks
# the size against GEO's own filelist.txt and writes a sha256 for each of the 1,378 IDATs, which
# every task re-checks before it runs. The annotation is the series-matrix header; GEO regenerates
# that file, so the pin is on the derived pheno.tsv (content), not on GEO's bytes.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$BUCKET/inputs/methylation-array-real"
pin() { awk -v f="$1" '$2==f{print $1}' "$HERE/pins.sha256"; }

shacheck() { [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$(pin "$(basename "$1")")" ] || { echo "FAIL: $1 is not its pin" >&2; exit 1; }; }
for f in fetch.sh sesame-cache.sh run.sh minfi-real.R sesame-real.R; do shacheck "$HERE/$f"; aws s3 cp --only-show-errors "$HERE/$f" "$P/$f"; done

if ! aws s3 ls "$P/GSE42861/pheno.tsv" >/dev/null 2>&1; then
  spec="$(mktemp)"; sed "s|\${COOKBOOK_BUCKET}|${BUCKET#s3://}|g" "$HERE/fetch.task.json" > "$spec"
  spawn task run --spec "$spec" --wait      # ~5 min on r8g.xlarge: 6.17 GB from NCBI in ~2 min
fi
# SeSAMe's inferSex needs one ExperimentHub resource beyond the recipe's cache (probeIDSignature);
# sesame-cache.task.json adds it to the recipe's pinned cache, and task 2 checks all five blobs.
if ! aws s3 ls "$P/sesame-cache/sesame-cache-real.tar" >/dev/null 2>&1; then
  spec="$(mktemp)"; sed "s|\${COOKBOOK_BUCKET}|${BUCKET#s3://}|g" "$HERE/sesame-cache.task.json" > "$spec"
  spawn task run --spec "$spec" --wait
fi
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
for f in pheno.tsv idat.sha256 raw.sha256; do aws s3 cp --only-show-errors "$P/GSE42861/$f" "$W/$f"; done
shacheck "$W/pheno.tsv"; shacheck "$W/idat.sha256"
[ "$(cut -d' ' -f1 "$W/raw.sha256")" = "$(pin GSE42861_RAW.tar)" ] || { echo "FAIL: GEO's archive is not the pinned one" >&2; exit 1; }
echo "staged to $P/ -- archive, annotation, 1,378 IDAT sums and scripts all match pins.sha256"
