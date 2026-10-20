#!/usr/bin/env bash
# Pull the three chips' evidence into results/<chip>/ and write the cross-chip comparison.
#
#   bash collect.sh <bucket> <nonce>      # the nonce given to spec_arch.py for both specs
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the runs were launched with}"
ACCT="$(aws sts get-caller-identity --query Account --output text)"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs s="" ;; x86_64) p=runs-x86_64 s=-x8664 ;; amd) p=runs-amd s=-amd ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in ref.json host.txt times.txt ncref.log; do
    aws s3 cp --only-show-errors "s3://$BUCKET/$p/zarr-dask-real/ncref/$f" "$HERE/results/$chip/ncref-$f"
  done
  for f in summary.json host.txt times.txt real.log; do
    aws s3 cp --only-show-errors "s3://$BUCKET/$p/zarr-dask-real/zarr/$f" "$HERE/results/$chip/$f"
  done
  for t in ncref zarr; do
    id=cookbook-zarr-dask-real; [ $t = ncref ] && id=cookbook-zarr-dask-real-ncref
    aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$id$s-$NONCE/completion.json" \
      "$HERE/results/$chip/$t-completion.json"
  done
done
python3 -I "$HERE/compare.py" "$HERE/results" | tee "$HERE/results/compare.txt"
