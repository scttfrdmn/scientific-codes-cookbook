#!/usr/bin/env bash
# Stage wgcna-real. The data is measurements/glmnet-real's: the TCGA PanCanAtlas RNA-seq matrix
# (Hoadley et al. 2018, 20,531 genes x 11,069 samples) and its sample annotations, fetched on AWS by
# GDC file UUID and md5-checked there. Nothing is copied: the spec reads inputs/glmnet-real/pancan/
# directly, and checks both files against pins.sha256 at stage-in. This script stages the two
# scripts and the pins.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DATA="s3://$BUCKET/inputs/glmnet-real/pancan"
DST="s3://$BUCKET/inputs/wgcna-real"

if ! aws s3 ls --region "$REGION" "$DATA/data.sha256" >/dev/null 2>&1; then
  bash "$ROOT/measurements/glmnet-real/stage-inputs.sh" "$BUCKET"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DATA/data.sha256" - | awk '{print $1"  pancan/"$2}' | sort)"
want="$(grep ' pancan/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { printf 'FAIL: staged data is\n%s\npinned\n%s\n' "$got" "$want" >&2; exit 1; }
echo "data: 2 files match pins.sha256"
( cd "$HERE" && grep -E ' real\.(R|sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.R real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
