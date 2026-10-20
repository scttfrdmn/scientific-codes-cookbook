#!/usr/bin/env bash
# Stage measurements/cantera-real: the official GRI-Mech 3.0 files and its 24 flame-speed and
# ignition target pages, plus this directory's scripts.
#
# Tier: a durable public id -- GRI-Mech 3.0's own distribution (combustion.berkeley.edu/gri-mech,
# unchanged since its 1999 release). 27 files, 156 KB, each pinned by sha256 in sources.sha256 and
# re-checked in every run. These are reference files, not a dataset: they go straight to the bucket
# through a scratch directory that is removed on exit.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/cantera-real"
BASE=http://combustion.berkeley.edu/gri-mech/version30

tmp="$(mktemp -d "${HOME}/.xtmp/cantera-real-stage.XXXXXX" 2>/dev/null || mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/gri/targets30"
for f in grimech30.dat thermo30.dat transport.dat; do
  curl -fsS -m 60 -o "$tmp/gri/$f" "$BASE/files30/$f"
done
awk '{print $2}' "$HERE/sources.sha256" | grep '^targets30/' | while read -r p; do
  curl -fsS -m 60 -o "$tmp/gri/$p" "$BASE/$p"
done
( cd "$tmp/gri" && shasum -a 256 -c "$HERE/sources.sha256" >/dev/null ) \
  || { echo "FAIL: a fetched source does not match sources.sha256" >&2; exit 1; }
echo "fetched $(wc -l < "$HERE/sources.sha256") sources, all match sources.sha256"
aws s3 cp --region "$REGION" --only-show-errors --recursive "$tmp/gri/" "$DST/gri/"
for f in run.sh real.py targets.tsv sources.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged $DST/"
