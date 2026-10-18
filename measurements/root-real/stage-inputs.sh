#!/usr/bin/env bash
# Stage the root-real input and scripts.
#
#   bash measurements/root-real/stage-inputs.sh <bucket>
#
# Data: CMS Open Data record 12341, "DoubleMuParked dataset from 2012 in NanoAOD format reduced on
# muons" (DOI 10.7483/OPENDATA.CMS.LVG5.QT81) -- one file, 2,244,449,133 bytes, 61,540,413 events,
# the input of ROOT's own df102 tutorial. Tier: a DOI-identified public record. It is fetched by a
# spawn task ON AWS (00-fetch.task.json runs fetch.py, which checks the record's size and adler32
# while streaming) straight into the bucket, never to this machine; the sha256 it computes is
# then held to the committed pin here.
set -euo pipefail

BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOTDIR="$(cd "$HERE/../.." && pwd)"
P="s3://$BUCKET/inputs/root-real"
DATA=Run2012BC_DoubleMuParked_Muons.root

cd "$HERE"
grep -E ' (fetch\.py|real\.sh|analysis\.py|check\.py)$' pins.sha256 | shasum -a 256 -c
for f in fetch.py real.sh analysis.py check.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$P/$f"
done

if ! aws s3 ls --region "$REGION" "$P/data/$DATA" >/dev/null 2>&1; then
  spawn task run --spec "$(python3 "$ROOTDIR/scripts/spec_arch.py" root arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")" --wait
fi
got=$(aws s3 cp --region "$REGION" --only-show-errors "$P/data/$DATA.sha256" - | awk '{print $1}')
want=$(awk -v f="$DATA" '$2==f{print $1}' pins.sha256)
[ -n "$want" ] && [ "$got" = "$want" ] || { echo "FAIL: $DATA sha256 $got, pinned $want" >&2; exit 1; }
size=$(aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "inputs/root-real/data/$DATA" --query ContentLength --output text)
[ "$size" = 2244449133 ] || { echo "FAIL: $DATA is $size bytes in the bucket" >&2; exit 1; }
echo "$DATA sha256 $got == pin, $size bytes (record 12341)"
echo "staged to $P/"
