#!/usr/bin/env bash
# Stage arviz-real. The data is cmdstan-real's posteriordb 1.1.0 reference set (57 posteriors with
# their models, data, reference draws and reference info; 256 files, 32 MB), reused in place from
# s3://<bucket>/inputs/cmdstan-real/pdb/ -- run measurements/cmdstan-real/stage-inputs.sh first if
# it is not there. Its pins are cmdstan-real's, copied into pins.sha256 here and checked again at
# every stage-in. This script uploads only the two task scripts and the pin file.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/arviz-real"

got="$(aws s3 cp --region "$REGION" --only-show-errors "s3://$BUCKET/inputs/cmdstan-real/pdb/data.sha256" - | sort)"
want="$(grep ' pdb/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { echo "FAIL: the staged posteriordb files do not match pins.sha256" >&2; exit 1; }
echo "data: $(echo "$want" | grep -c .) files match pins.sha256"
( cd "$HERE" && grep -E ' (arviz_real\.py|real\.sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in arviz_real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
