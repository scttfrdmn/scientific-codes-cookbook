#!/usr/bin/env bash
# Stage openbabel-pdbfixer-real.
#
# Open Babel reads what rdkit-real already staged and produced -- nothing is copied twice:
#   inputs/rdkit-real/chembl37/chembl_37_chemreps.txt.gz   (ChEMBL 37, EBI-published sha256, pins.sha256)
#   runs/rdkit-real/chembl37/rdkit_real.tsv.gz             (RDKit's verified whole-release output; the
#                                                            task checks its uncompressed sha256 in-run)
# PDBFixer reads every mmCIF in hash directory `ab` of the RCSB 2026-01-01 snapshot on RODA
# (s3://pdbsnapshots, frozen, same region): 247 entries, copied S3-to-S3, nothing through this
# machine. The pin is the sha256 S3 computes server-side on each CopyObject, which must equal
# cohort.sha256; the run checks it again at stage-in. The 181 of these that cctbx-real also read are
# checked against cctbx-real's own cohort.sha256, so the two measurements read the same bytes.
#
#   bash stage-inputs.sh <bucket>            # copy, then verify against cohort.sha256
#   bash stage-inputs.sh <bucket> --pin      # first time only: write cohort.sha256 from S3
set -euo pipefail
BUCKET="${1:?pass your bucket}"
MODE="${2:-}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SNAP=20260101
SRCP="$SNAP/pub/pdb/data/structures/divided/mmCIF/ab"
DST="inputs/openbabel-pdbfixer-real/pdb-$SNAP/ab"

ids="$(aws s3 ls --no-sign-request --region "$REGION" "s3://pdbsnapshots/$SRCP/" \
       | awk '{print $4}' | sed -n 's/^\(....\)\.cif\.gz$/\1/p' | sort)"
echo "mmCIF entries in ab/: $(echo "$ids" | grep -c .)"
out=""
for id in $ids; do
  k="$DST/$id.cif.gz"
  if ! aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$k" >/dev/null 2>&1; then
    aws s3api copy-object --region "$REGION" --copy-source "pdbsnapshots/$SRCP/$id.cif.gz" \
      --bucket "$BUCKET" --key "$k" --checksum-algorithm SHA256 >/dev/null
  fi
  b64="$(aws s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$k" \
         --checksum-mode ENABLED --query ChecksumSHA256 --output text)"
  hex="$(printf '%s' "$b64" | base64 -d | od -An -tx1 | tr -d ' \n')"
  [ "${#hex}" = 64 ] || { echo "FAIL: no full-object sha256 for $k ($b64)" >&2; exit 1; }
  out="$out$hex  pdb/$id.cif.gz
"
done
got="$(printf '%s' "$out" | sort -k2)"
if [ "$MODE" = "--pin" ]; then
  printf '%s\n' "$got" > "$HERE/cohort.sha256"
  echo "wrote cohort.sha256 ($(grep -c . "$HERE/cohort.sha256") files)"
fi
[ "$got" = "$(sort -k2 "$HERE/cohort.sha256")" ] || { echo "FAIL: staged cohort does not match cohort.sha256" >&2; exit 1; }
echo "data: $(grep -c . "$HERE/cohort.sha256") files match cohort.sha256"
shared="$(sed -n 's|  pdb/mmCIF/|  pdb/|p' "$HERE/../cctbx-real/cohort.sha256" | LC_ALL=C sort)"
[ -z "$(LC_ALL=C comm -23 <(printf '%s\n' "$shared") <(LC_ALL=C sort "$HERE/cohort.sha256"))" ] \
  || { echo "FAIL: an entry cctbx-real also read has different bytes" >&2; exit 1; }
echo "same bytes as cctbx-real: $(printf '%s\n' "$shared" | grep -c .) entries"

aws s3 ls --region "$REGION" "s3://$BUCKET/runs/rdkit-real/chembl37/rdkit_real.tsv.gz" >/dev/null \
  || { echo "FAIL: run rdkit-real first; this measurement reads its output" >&2; exit 1; }
( cd "$HERE" && shasum -a 256 ob_real.py fix_real.py real.sh > scripts.sha256 )
for f in ob_real.py fix_real.py real.sh cohort.sha256 scripts.sha256 pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "s3://$BUCKET/inputs/openbabel-pdbfixer-real/$f"
done
echo "staged to s3://$BUCKET/inputs/openbabel-pdbfixer-real/"
