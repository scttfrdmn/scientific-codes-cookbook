#!/usr/bin/env bash
# Stage this measurement's scripts beside DESS66 (00-fetch.task.json put the zips there, on AWS).
# The specs pin each script by sha256, so a stale copy fails the task before any science runs.
set -euo pipefail
B=${1:?bucket}
D=$(cd "$(dirname "$0")" && pwd)
for f in run.sh psi4_s66.py check_s66.py; do
  aws s3 cp "$D/$f" "s3://$B/inputs/psi4-real/$f"
done
aws s3 ls "s3://$B/inputs/psi4-real/dess66/DESS66.zip"
