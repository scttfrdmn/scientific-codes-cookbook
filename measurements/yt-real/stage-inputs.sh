#!/usr/bin/env bash
# Stage yt-real: every output of yt's Enzo_64 sample run (Enzo, 64^3 root grid, AMR, star
# formation, 128 Mpc/h) plus the two scripts the task runs.
#
# Tier: a durable public id. Enzo_64.tar.gz on yt's sample-data hub is pinned by the sha256 that
# yt 4.4.2's own yt/sample_data_registry.json carries. It is fetched ON AWS by 00-fetch.task.json
# (nothing passes through this machine), checked there against that sha256, unpacked, and kept in
# the bucket with a per-file sha256 manifest. This script checks that manifest against the
# committed files.sha256, and the real task checks every staged byte against it again.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/yt-real"

if ! aws s3 ls --region "$REGION" "$DST/files.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" yt arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
aws s3 cp --region "$REGION" --only-show-errors "$DST/files.sha256" - | cmp -s - "$HERE/files.sha256" \
  || { echo "FAIL: the bucket's manifest is not the committed files.sha256" >&2; exit 1; }
echo "data: $(wc -l < "$HERE/files.sha256") files, manifest matches files.sha256"
for f in real.py run.sh; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
