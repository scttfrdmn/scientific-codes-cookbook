#!/usr/bin/env bash
# Pull the three chips' evidence into results/<chip>/ and write the cross-chip comparison.
#
#   bash collect.sh <bucket> <nonce>      # the nonce given to spec_arch.py for real.task.json
set -euo pipefail
BUCKET="${1:?pass your bucket}"
NONCE="${2:?pass the nonce the runs were launched with}"
ACCT="$(aws sts get-caller-identity --query Account --output text)"
HERE="$(cd "$(dirname "$0")" && pwd)"
for chip in arm64 x86_64 amd; do
  case $chip in arm64) p=runs t=cookbook-vina-real-posebusters-$NONCE ;;
                x86_64) p=runs-x86_64 t=cookbook-vina-real-posebusters-x8664-$NONCE ;;
                amd) p=runs-amd t=cookbook-vina-real-posebusters-amd-$NONCE ;; esac
  mkdir -p "$HERE/results/$chip"
  for f in results.tsv metrics.txt checks.txt host.txt times.txt; do
    aws s3 cp --only-show-errors "s3://$BUCKET/$p/vina-real/posebusters/$f" "$HERE/results/$chip/$f"
  done
  aws s3 cp --only-show-errors "s3://spawn-results-$ACCT-us-west-2/tasks/$t/completion.json" "$HERE/results/$chip/completion.json"
done
python3 -I "$HERE/compare.py" "$HERE/results" | tee "$HERE/results/compare.txt"
