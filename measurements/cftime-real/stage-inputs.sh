#!/usr/bin/env bash
# Stage the cftime real workload: the Pangeo CMIP6 catalog, pinned, and this directory's scripts.
#
#   bash stage-inputs.sh <bucket>          # catalog + scripts; then run 00-fetch once
#   bash stage-inputs.sh <bucket> axes     # after 00-fetch: pin the fetched axes for the decode runs
#
# The catalog is copied server-side from s3://cmip6-pds (AWS Open Data, us-west-2, versioned) at a
# fixed VersionId and checked against its sha256. 00-fetch.task.json then reads every store's time
# axis from cmip6-pds, in-region, into inputs/cftime-real/axes/ -- nothing is downloaded locally.
set -euo pipefail

B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
P="s3://$B/inputs/cftime-real"
CAT_KEY=pangeo-cmip6.csv.gz
CAT_VER=KCB936FiD1GjuGHQxrzBPr.RL.MsTp8U
CAT_SHA=48afd7ec7fb5695b4db5cdb2cf33fa858960458ee2aa792ea3d410c9a378db88

if [ "${2:-}" = axes ]; then
  aws s3 cp --region "$REGION" --only-show-errors "$P/axes.sha256" "$D/axes.sha256"
  n="$(wc -l < "$D/axes.sha256" | tr -d ' ')"
  echo "pinned $n shard files from $P/axes/ into axes.sha256"
  echo "$(shasum -a 256 "$D/axes.sha256" | cut -d' ' -f1)  axes.sha256" >> "$D/pins.sha256"
  aws s3 cp --region "$REGION" --only-show-errors "$D/pins.sha256" "$P/pins.sha256"
  cat "$D/pins.sha256"
  exit 0
fi

aws s3api copy-object --region "$REGION" --bucket "$B" --key "inputs/cftime-real/$CAT_KEY" \
  --copy-source "cmip6-pds/$CAT_KEY?versionId=$CAT_VER" > /dev/null
got="$(aws s3 cp --region "$REGION" --only-show-errors "$P/$CAT_KEY" - | shasum -a 256 | cut -d' ' -f1)"
[ "$got" = "$CAT_SHA" ] || { echo "FAIL: catalog sha256 $got, pinned $CAT_SHA"; exit 1; }
echo "catalog $CAT_KEY@$CAT_VER sha256 ok"

for f in fetch.py decode.py refcal.py check.py; do
  aws s3 cp --region "$REGION" --only-show-errors "$D/$f" "$P/$f"
done
{ echo "$CAT_SHA  $CAT_KEY"; ( cd "$D" && shasum -a 256 fetch.py decode.py refcal.py check.py ); } > "$D/pins.sha256"
aws s3 cp --region "$REGION" --only-show-errors "$D/pins.sha256" "$P/pins.sha256"
cat "$D/pins.sha256"
echo "staged to $P/"
