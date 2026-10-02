#!/usr/bin/env bash
# Stage the Kraken2 standard-8 index into your bucket.
#
# WHY standard-8 AND NOT PlusPF. standard-8 (~5.5 GiB compressed, ~7.6 GB of .k2d) is the
# index most people actually run: it fits a laptop and a modest instance. PlusPF is ~70 GB
# and would force an EFS or FUSE mount -- and picking a 70 GB database so the recipe can
# demonstrate a mount is choosing the input to suit the mechanism, which is backwards. If a
# data-path variant is wanted, PlusPF is the condition that unlocks it.
#
# The date in the name is a durable id (the upstream bucket keeps every dated build), but it
# is not a byte guarantee -- the task prints the archive sha256 so anyone can check they got
# the same index.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
IDX="k2_standard_08gb_20250402.tar.gz"
SRC="s3://genome-idx/kraken/$IDX"

# Server-side copy: 5.5 GiB never touches this machine. NOTE: --no-sign-request cannot be
# used here. It applies to BOTH ends, so the destination write becomes anonymous and the
# multipart upload fails with "Anonymous users cannot initiate multipart uploads". The public
# source bucket serves authenticated reads too, so signing both ends is the working path.
aws s3 cp "$SRC" "s3://$BUCKET/inputs/kraken2/$IDX" --only-show-errors

aws s3 ls "s3://$BUCKET/inputs/kraken2/$IDX" --human-readable
echo "--- reads come from the bwa recipe; run: make stage RECIPE=bwa-samtools"
