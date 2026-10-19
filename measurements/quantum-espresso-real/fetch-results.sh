#!/usr/bin/env bash
# Pull one run's outputs from all three chips into results/<chip>/, then compare them.
#   fetch-results.sh <bucket> <run label, e.g. r2>
set -euo pipefail
B="${1:?bucket}"; RUN="${2:?run label}"
D="$(cd "$(dirname "$0")" && pwd)"
for p in arm64:runs x86_64:runs-x86_64 amd:runs-amd; do
  c=${p%%:*}; pre=${p#*:}
  mkdir -p "$D/results/$c"
  aws s3 cp "s3://$B/$pre/quantum-espresso-real/$RUN/" "$D/results/$c/" --recursive --only-show-errors --exclude '*.tar'
done
python3 "$D/collect.py" "$D/results"
