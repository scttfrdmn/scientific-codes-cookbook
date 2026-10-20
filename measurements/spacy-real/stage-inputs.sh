#!/usr/bin/env bash
# Stage measurements/spacy-real: a 100,000-id block of PMC Open Access full text (RODA), UD English-EWT
# r2.18, the CoNLL 2018 scorer, and this directory's scripts.
#
# Tier: RODA (s3://pmc-oa-opendata) for the corpus -- every article's text is kept only if it matches
# the md5 in PMC's own metadata/<PMCID>.<version>.json -- and durable public ids for the rest: EWT at a
# release tag (each file checked against the tag's git blob id), the scorer at a commit (sha256).
# 00-fetch.task.json does the downloading ON AWS; this script only uploads scripts and pins.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/spacy-real"
aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
aws s3 ls --region "$REGION" "$DST/pmc/shards.sha256" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" spacy arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
for pair in "pmc/shards.sha256:shards.sha256" "fetched-ewt.sha256:ewt.sha256"; do
  src="${pair%%:*}"; pin="$HERE/${pair##*:}"
  got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/$src" -)"
  if [ -f "$pin" ]; then
    [ "$got" = "$(cat "$pin")" ] || { echo "FAIL: staged $src does not match $(basename "$pin")" >&2; exit 1; }
  else
    printf '%s\n' "$got" > "$pin"
    echo "pinned $(wc -l < "$pin") files into $(basename "$pin")"
  fi
done
for f in run.sh real.py ewt.py shards.sha256 ewt.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged $DST/"
