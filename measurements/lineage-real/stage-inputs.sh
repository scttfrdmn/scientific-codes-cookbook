#!/usr/bin/env bash
# Stage lineage-real: Nextstrain's open SARS-CoV-2 release (every INSDC genome, with its strain
# name) at pinned S3 object versions, copied SERVER-SIDE into the cookbook bucket, plus this
# directory's scripts. No genome passes through the machine running this.
#
# DATA TIER: durable public ids.
#   nextstrain-data (public, us-east-1, S3 versioning on), files/ncov/open/, the 2026-10-17 release:
#     sequences.fasta.zst        versionId eRPZEX0O6RHuvlQA7pRS9hPHMUPxWI4k  1,292,883,288 bytes
#     metadata.tsv.zst           versionId slJwcw736Vtlg.9XAoCGq9BLi8CSSxNR    952,159,038 bytes
#     cog_uk_accessions.tsv.zst  versionId ZuQ8sI1jfofiSMN5AbROB7gqi5TMapeV    141,838,912 bytes
#   Task 00 sha256-pins these on the box and writes the chosen genomes out as INSDC
#   accession.version ids, so the set can be re-fetched from ENA/GenBank after Nextstrain
#   expires these versions.
#   The truth labels (pango-designation lineages.csv) and the alias table are fetched by task 00
#   from GitHub at tag v1.38 (commit 5d58f88f6d2649de35a83951938e047006febb45) and sha256-pinned
#   there. v1.38 because it is the designation release the pinned pangolin image's pangolin-data
#   (PUSHER-v1.38) was built from; a later release labels lineages that tree does not contain.
#   stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
DST="inputs/lineage-real"
HERE="$(cd "$(dirname "$0")" && pwd)"
for kv in "sequences.fasta.zst=eRPZEX0O6RHuvlQA7pRS9hPHMUPxWI4k=1292883288" \
          "metadata.tsv.zst=slJwcw736Vtlg.9XAoCGq9BLi8CSSxNR=952159038" \
          "cog_uk_accessions.tsv.zst=ZuQ8sI1jfofiSMN5AbROB7gqi5TMapeV=141838912"; do
  f="${kv%%=*}"; rest="${kv#*=}"; v="${rest%%=*}"; n="${rest#*=}"
  got=$(aws s3api head-object --bucket "$BUCKET" --key "$DST/nextstrain/$f" --query ContentLength --output text 2>/dev/null || true)
  if [ "$got" = "$n" ]; then echo "  $f already staged ($n bytes)"; continue; fi
  aws s3api copy-object --bucket "$BUCKET" --key "$DST/nextstrain/$f" \
    --copy-source "nextstrain-data/files/ncov/open/$f?versionId=$v" --region us-west-2 >/dev/null
  got=$(aws s3api head-object --bucket "$BUCKET" --key "$DST/nextstrain/$f" --query ContentLength --output text)
  test "$got" = "$n" || { echo "FAIL: $f is $got bytes, the pinned version is $n" >&2; exit 1; }
  echo "  $f  version $v  $n bytes"
done
shopt -s nullglob
for f in "$HERE"/*.sh "$HERE"/*.py; do
  case "$(basename "$f")" in stage-inputs.sh|mkspec.py|collect.sh|steps.py) continue;; esac
  aws s3 cp --only-show-errors "$f" "s3://$BUCKET/$DST/$(basename "$f")"
  shasum -a 256 "$f"
done
