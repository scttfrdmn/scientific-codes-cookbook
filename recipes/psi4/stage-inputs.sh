#!/usr/bin/env bash
# Stage the two scripts. No data: the H2 geometry is inline in h2.py. The task checks each
# script against the sha256 written in its own command, so a stale staged copy fails, not runs.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=psi4 does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for f in h2.py smoke.py; do
  aws s3 cp --region "$REGION" "$SCRIPT_DIR/$f" "$BUCKET/inputs/psi4/$f" >/dev/null
  echo "  $BUCKET/inputs/psi4/$f  $(shasum -a 256 "$SCRIPT_DIR/$f" | cut -d' ' -f1)"
done
