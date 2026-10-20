#!/usr/bin/env bash
# Stage measurements/plumed-real: 16,000 sander trajectories from RODA's "Conformational Space of
# Short Peptides" (s3://toyokounqpeptides, us-west-2), plus this directory's scripts.
#
# Tier: RODA. 00-fetch.task.json downloads the cohort ON AWS (nothing passes through this
# machine), checks every object's MD5 against the ETag RODA serves, and bundles the untouched
# .tar.bz2 objects into one reproducible tar. Its sha256 and the per-object manifest's are
# pinned in inputs.sha256, and every run re-checks both before reading anything.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/plumed-real"

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
aws s3 ls --region "$REGION" "$DST/cohort/cohort.tar" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" plumed arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/cohort/cohort.sha256" - | awk '{print $1}')"
want="$(awk '$2 == "cohort.tar" {print $1}' "$HERE/inputs.sha256")"
[ "$got" = "$want" ] || { echo "FAIL: staged cohort is $got, pinned $want" >&2; exit 1; }

for f in inputs.sha256 run.sh real.py; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
