#!/usr/bin/env bash
# Stage glmnet-real: the TCGA PanCanAtlas RNA-seq matrix (Hoadley et al. 2018, 20,531 genes x 11,069
# samples, 1.88 GB) and its sample annotations, plus the two scripts the task runs.
#
# Tier: a durable public id. Both files are GDC PanCanAtlas publication files, addressed by GDC file
# UUID. They are fetched ON AWS by 00-fetch.task.json (nothing passes through this machine), checked
# there against the md5 in each file's GDC record, and kept in the bucket with their sha256. This
# script then checks that sha256 against pins.sha256, which the real task checks again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/glmnet-real"
DATA=EBPlusPlusAdjustPANCAN_IlluminaHiSeq_RNASeqV2.geneExp.tsv

if ! aws s3 ls --region "$REGION" "$DST/pancan/$DATA" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" glmnet arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
# the fetch task's own sha256 of what it kept must equal the pins
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/pancan/data.sha256" - | awk '{print $1"  pancan/"$2}' | sort)"
want="$(grep ' pancan/' "$HERE/pins.sha256" | sort)"
[ "$got" = "$want" ] || { printf 'FAIL: staged data is\n%s\npinned\n%s\n' "$got" "$want" >&2; exit 1; }
echo "data: 2 files match pins.sha256"
( cd "$HERE" && grep -E ' real\.(R|sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.R real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
