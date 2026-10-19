#!/usr/bin/env bash
# Stage the fenicsx-real task scripts. No data: the reference is a manufactured solution, so the
# mesh, the forms and the exact answer are built in-code (data tier: synthetic). The task
# re-checks both scripts against pins.sha256 before anything runs.
#
#   bash measurements/fenicsx-real/stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
python3 -I -c "import ast; ast.parse(open('poisson3d.py').read())"
shasum -a 256 -c pins.sha256
shasum -a 256 -c fpcontract.sha256          # the attribution probe (fpcontract.task.json)
for f in poisson3d.py real.sh pins.sha256 fpcontract.py fpcontract.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "s3://$BUCKET/inputs/fenicsx-real/$f"
done
echo "staged to s3://$BUCKET/inputs/fenicsx-real/"
