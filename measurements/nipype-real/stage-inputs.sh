#!/usr/bin/env bash
# Stage measurements/nipype-real: the scripts only.
#
# THE DATA is measurements/neuroimaging-real's, reused in place: the Enhanced Rockland Sample's
# baseline DIFF_137_AP diffusion scan for 123 NFBS subjects (RODA, s3://fcp-indi), staged by that
# measurement's stage-inputs.sh and pinned by its dwi.sha256, which run.sh asserts on every box
# before any compute. The reference maps the workflow is compared against are that measurement's
# DIPY outputs on the same chip (runs*/neuroimaging-real/cohort/dipy/dipy-maps.tar).
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
DIR="$(cd "$(dirname "$0")" && pwd)"

n=$(aws s3 ls "$BUCKET/inputs/neuroimaging-real/dwi/" --region "$REGION" | wc -l | tr -d ' ')
test "$n" = 492 || { echo "stage measurements/neuroimaging-real first ($n of 492 DWI files)" >&2; exit 1; }
for a in runs runs-x86_64 runs-amd; do
  aws s3 ls "$BUCKET/$a/neuroimaging-real/cohort/dipy/dipy-maps.tar" --region "$REGION" >/dev/null \
    || echo "  note: no $a/neuroimaging-real reference maps yet -- run that measurement on this chip first" >&2
done
for s in run.sh pipeline.py check.py hashprobe.py xchip.py; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$DIR/$s" 2>/dev/null || [ "$s" = run.sh ]
  aws s3 cp "$DIR/$s" "$BUCKET/inputs/nipype-real/$s" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/nipype-real/  (scripts; the cohort is neuroimaging-real's)"
