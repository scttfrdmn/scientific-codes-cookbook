#!/usr/bin/env bash
# Stage the humann-real inputs.
#
#   reads     metaphlan-real's ERR2984773 (ZymoBIOMICS D6300, PRJEB29504, RODA), already in the
#             bucket and pinned there -- shared, not copied
#   profile   metaphlan-real's MetaPhlAn profile of the same reads (byte-identical on all three
#             chips), copied server-side to inputs/humann-real/
#   databases HUMAnN 3.9's full ChocoPhlAn v201901_v31 and UniRef90 v201901b, streamed from the
#             bioBakery server into the bucket by 00-dbs on a box -- nothing passes through this
#             machine. The tarballs' sha256 is printed by the task; every extracted file is in
#             db.sha256, whose own sha256 is in pins.sha256.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
P="$BUCKET/inputs/humann-real"

aws s3 ls --region "$REGION" "$BUCKET/inputs/metaphlan-real/prep/ERR2984773/ERR2984773_2.fastq.gz" >/dev/null \
  || { echo "stage metaphlan-real first: bash measurements/metaphlan-real/stage-inputs.sh" >&2; exit 1; }
aws s3 cp --only-show-errors --region "$REGION" \
  "$BUCKET/runs/metaphlan-real/zymo/ERR2984773/profile.txt" "$P/ERR2984773.profile.txt"
for f in fetch_dbs.py hashtree.py run.sh check.py pins.sha256; do
  aws s3 cp --only-show-errors --region "$REGION" "$HERE/$f" "$P/$f"
done

if ! aws s3 ls --region "$REGION" "$P/db.sha256" >/dev/null 2>&1; then
  spec=$(python3 "$ROOT/scripts/spec_arch.py" humann arm64 "${BUCKET#s3://}" "$(date +%s)" "$HERE/00-dbs.task.json")
  spawn task run --spec "$spec" --region "$REGION" --wait
  aws s3 cp --only-show-errors --region "$REGION" "$P/db.sha256" - | shasum -a 256 \
    | awk '{print "add to pins.sha256: " $1 "  db.sha256"}'
fi
aws s3 cp --only-show-errors --region "$REGION" "$P/fetch-chocophlan.txt" -
aws s3 cp --only-show-errors --region "$REGION" "$P/fetch-uniref.txt" -
