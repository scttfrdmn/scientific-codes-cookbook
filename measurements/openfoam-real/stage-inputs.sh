#!/usr/bin/env bash
# Stage openfoam-real: this directory's scripts. The grid is NOT re-staged: it is su2-real's
# n0012_897-257.su2 (NASA TMR's 897x257 C-grid, su2code/TestCases v8.5.0, git-blob-checked there),
# read from inputs/su2-real/data/ so OpenFOAM and SU2 solve on the same bytes. The task checks
# every file, grid included, against pins.sha256 at stage-in.
#
#   bash stage-inputs.sh <bucket>        (after measurements/su2-real/stage-inputs.sh)
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/openfoam-real"
grid="$(aws s3 cp --region "$REGION" --only-show-errors "s3://$BUCKET/inputs/su2-real/data/data.sha256" - | grep ' data/n0012_897-257.su2$')"
[ "$grid" = "$(grep ' data/n0012_897-257.su2$' "$HERE/pins.sha256")" ] || { echo "FAIL: su2-real's staged grid is not the pinned one" >&2; exit 1; }
( cd "$HERE" && grep -v ' data/' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in run.sh case.sh su2ToFoam.awk pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
