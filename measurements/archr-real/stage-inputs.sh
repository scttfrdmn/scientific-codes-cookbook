#!/usr/bin/env bash
# Stage measurements/archr-real. The dataset is signac-real's, already in the bucket and pinned
# (inputs/signac-real/: the 10x 10k_pbmc_ATACv2 fragments, index, peaks, cells, Ensembl 98 exons),
# so nothing is fetched here. This builds the recount, uploads it with the scripts, and launches
# 01-reference.task.json, which fetches the chrom sizes and blacklist on AWS and runs the recount.
#
# Usage: stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
D="$(cd "$(dirname "$0")" && pwd)"
BIN="${TMPDIR:-/tmp}/recount-linux-arm64"
(cd "$D/recount" && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags=-buildid= -o "$BIN" .)
aws s3 cp "$BIN" "s3://$B/inputs/archr-real/recount-linux-arm64" --only-show-errors
for f in reference.sh pins.sha256 archr-real.R; do
  aws s3 cp "$D/$f" "s3://$B/inputs/archr-real/$f" --only-show-errors
done
sed "s|\${COOKBOOK_BUCKET}|$B|g" "$D/01-reference.task.json" > "${TMPDIR:-/tmp}/archr-real-reference.json"
spawn task run --spec "${TMPDIR:-/tmp}/archr-real-reference.json" --wait
aws s3 ls "s3://$B/inputs/archr-real/" --recursive
