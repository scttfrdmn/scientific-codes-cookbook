#!/usr/bin/env bash
# Stage petsc-real: six SPD matrices from the SuiteSparse Matrix Collection, as the collection
# publishes them (Matrix Market .tar.gz), plus the task's scripts.
#
# Tier: a durable public id. Each matrix has a permanent collection id and URL
# (https://sparse.tamu.edu/<group>/<name>; Davis & Hu, ACM TOMS 38(1), doi:10.1145/2049662.2049663).
# The archives are fetched ON AWS by 00-fetch.task.json and kept only if their size and entry count
# match the collection's; the fetch writes their sha256, which must equal pins.sha256 here, and the
# real task checks pins.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/petsc-real"

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
if ! aws s3 ls --region "$REGION" "$DST/mm/data.sha256" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" petsc arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/mm/data.sha256" - | sort)"
want="$(grep ' mm/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { echo "FAIL: the staged matrices do not match pins.sha256" >&2; exit 1; }
echo "data: $(echo "$want" | grep -c .) matrices match pins.sha256"
( cd "$HERE" && grep -E ' (convert|solve)\.py$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in run.sh convert.py solve.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
