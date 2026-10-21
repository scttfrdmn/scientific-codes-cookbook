#!/usr/bin/env bash
# Stage the calculix-real task scripts. No data: NAFEMS LE10 is defined by its geometry, loads and
# published target, so le10.py builds the mesh in-task (data tier: synthetic, against a published
# reference). The task re-checks both scripts against pins.sha256 before anything runs.
#
#   bash measurements/calculix-real/stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
cd "$(cd "$(dirname "$0")" && pwd)"
python3 -I -c "import ast; ast.parse(open('le10.py').read())"
bash -n real.sh
shasum -a 256 -c pins.sha256
for f in le10.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "s3://$BUCKET/inputs/calculix-real/$f"
done
echo "staged to s3://$BUCKET/inputs/calculix-real/"
