#!/usr/bin/env bash
# Stage cctbx-real: every PDB entry in hash directory `ab` of the RCSB 2026-01-01 snapshot that
# deposited structure factors, with its model -- copied S3-to-S3 from RODA (s3://pdbsnapshots, the
# frozen yearly archive, same region), nothing through this machine.
#
# Tier: RODA. The snapshot is dated and frozen, so the path is the durable id; the pin is the
# sha256 S3 computes server-side on each CopyObject (single-part, so a full-object digest), which
# must equal cohort.sha256 here. The run checks cohort.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>            # copy, then verify against cohort.sha256
#   bash stage-inputs.sh <bucket> --pin      # first time only: write cohort.sha256 from S3
set -euo pipefail
BUCKET="${1:?pass your bucket}"
MODE="${2:-}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SNAP=20260101
SRC=pdbsnapshots
SRCP="$SNAP/pub/pdb/data/structures/divided"
DST="inputs/cctbx-real/pdb-$SNAP/ab"

ids="$(aws s3 ls --no-sign-request --region "$REGION" "s3://$SRC/$SRCP/structure_factors/ab/" \
       | awk '{print $4}' | sed -n 's/^r\(....\)sf\.ent\.gz$/\1/p' | sort)"
echo "entries with structure factors in ab/: $(echo "$ids" | grep -c .)"

keys=""
for id in $ids; do
  keys="$keys sf/r${id}sf.ent.gz:structure_factors/ab/r${id}sf.ent.gz mmCIF/${id}.cif.gz:mmCIF/ab/${id}.cif.gz"
done

out=""
for kv in $keys; do
  dk="${kv%%:*}"; sk="${kv#*:}"
  if ! aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$DST/$dk" >/dev/null 2>&1; then
    aws s3api copy-object --region "$REGION" --copy-source "$SRC/$SRCP/$sk" \
      --bucket "$BUCKET" --key "$DST/$dk" --checksum-algorithm SHA256 >/dev/null
  fi
  b64="$(aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$DST/$dk" \
         --checksum-mode ENABLED --query ChecksumSHA256 --output text)"
  hex="$(printf '%s' "$b64" | base64 -d | od -An -tx1 | tr -d ' \n')"
  [ "${#hex}" = 64 ] || { echo "FAIL: no full-object sha256 for $dk ($b64)" >&2; exit 1; }
  out="$out$hex  pdb/$dk
"
done
got="$(printf '%s' "$out" | sort -k2)"

if [ "$MODE" = "--pin" ]; then
  printf '%s\n' "$got" > "$HERE/cohort.sha256"
  echo "wrote cohort.sha256 ($(grep -c . "$HERE/cohort.sha256") files)"
fi
[ "$got" = "$(sort -k2 "$HERE/cohort.sha256")" ] || { echo "FAIL: staged cohort does not match cohort.sha256" >&2; exit 1; }
echo "data: $(grep -c . "$HERE/cohort.sha256") files match cohort.sha256"

( cd "$HERE" && shasum -a 256 real.py real.sh > scripts.sha256 )
for f in real.py real.sh cohort.sha256 scripts.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "s3://$BUCKET/inputs/cctbx-real/$f"
done
echo "staged to s3://$BUCKET/inputs/cctbx-real/"
