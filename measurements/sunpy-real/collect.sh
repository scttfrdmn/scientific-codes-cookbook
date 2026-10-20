#!/usr/bin/env bash
# Pull each chip's evidence into results/<chip>/ and compare every chip with Graviton4.
#
#   bash measurements/sunpy-real/collect.sh <bucket> <task nonce>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the specs were resolved with}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs; id=cookbook-sunpy-real-r1-$NONCE ;; *) p=runs-$chip; id=cookbook-sunpy-real-r1-${chip/_/}-$NONCE ;; esac
  aws s3 ls "s3://$BUCKET/$p/sunpy-real/r1/check.txt" >/dev/null 2>&1 || { echo "$chip: no run yet"; continue; }
  mkdir -p "$HERE/results/$chip"
  aws s3 cp --recursive --only-show-errors --exclude 'staged.sha256' "s3://$BUCKET/$p/sunpy-real/r1/" "$HERE/results/$chip/"
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I "$HERE/compare.py" "$HERE/results" | tee "$HERE/results/compare.txt"
