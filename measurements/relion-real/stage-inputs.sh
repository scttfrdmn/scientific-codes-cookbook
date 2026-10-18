#!/usr/bin/env bash
# Stage measurements/relion-real: the RELION 5.0 tutorial's first gold-standard refinement --
# its inputs (4,452 re-extracted beta-galactosidase particles, the Class3D reference it started
# from) and the depositors' own outputs (Refine3D, mask, PostProcess) to compare against.
#
# The 11.3 GB source tarball is streamed and filtered on AWS by fetch.task.json (fetch.sh), not
# here: this script only uploads the pins and scripts, then launches that task. Every byte that
# reaches the bucket is checked against inputs.sha256, and the tarball against its own sha256.
#
# Usage: stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
D="$(cd "$(dirname "$0")" && pwd)"
for f in inputs.sha256 fetch.sh run.sh check.py; do
  aws s3 cp "$D/$f" "s3://$B/inputs/relion-real/$f" --only-show-errors
done
sed "s|\${COOKBOOK_BUCKET}|$B|g" "$D/fetch.task.json" > /tmp/relion-real-fetch.json
spawn task run --spec /tmp/relion-real-fetch.json --wait
echo "staged: $(aws s3 ls "s3://$B/inputs/relion-real/Tutorial5.0/" --recursive | wc -l) files (want 40)"
