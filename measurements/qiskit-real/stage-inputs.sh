#!/usr/bin/env bash
# Stage measurements/qiskit-real: the QASMBench suite (pnnl/QASMBench at commit 357b942, the 132
# original circuits plus qelib1.inc, README, LICENSE, NOTICE) and this directory's scripts.
#
# Tier: a durable public id -- a git commit. QASMBench has no release or DOI, but a commit's tree
# pins every file's bytes by its git blob id. 00-fetch.task.json downloads the commit tarball ON
# AWS, keeps a file only if its blob id matches qasmbench-357b942.gitblobs (from the GitHub tree
# API), and writes the sha256 manifest that qasmbench.sha256 pins and every run re-checks.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/qiskit-real"
for f in fetch.py qasmbench-357b942.gitblobs run.sh real.py qasmbench.sha256; do
  [ -f "$HERE/$f" ] && aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
aws s3 ls --region "$REGION" "$DST/qasmbench/qasmbench.sha256" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" qiskit arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/qasmbench/qasmbench.sha256" -)"
if [ -f "$HERE/qasmbench.sha256" ]; then
  [ "$got" = "$(cat "$HERE/qasmbench.sha256")" ] || { echo "FAIL: staged suite does not match qasmbench.sha256" >&2; exit 1; }
else
  printf '%s\n' "$got" > "$HERE/qasmbench.sha256"
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/qasmbench.sha256" "$DST/qasmbench.sha256"
  echo "pinned $(wc -l < "$HERE/qasmbench.sha256") files into qasmbench.sha256"
fi
echo "staged $DST/"
