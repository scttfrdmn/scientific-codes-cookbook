#!/usr/bin/env bash
# Stage measurements/blast-real: NCBI's preformatted SwissProt BLAST database (the 2026-10-10
# snapshot on RODA, s3://ncbi-blast-databases) plus taxonomy4blast.sqlite3, copied S3-to-S3 into
# the bucket -- nothing is downloaded locally. Each object is copied with CopyObject and
# --checksum-algorithm SHA256, so S3 itself computes a full-object sha256 of the copy; that value is
# checked against inputs.sha256. The task checks the bytes again with sha256sum.
#
# RODA keeps only the last three snapshots, so the RODA path is not durable: the pin is the sha256
# set in inputs.sha256, and the bucket copy is what keeps those bytes. A later snapshot is a
# different database (a pin swap) -- every assertion in the README would need re-running.
#   stage-inputs.sh <bucket>          (PIN=0: print the sums instead of checking them)
set -euo pipefail
B="${1:?pass your bucket}"
SNAP=2026-10-10-01-05-01
P="inputs/blast-real/swissprot-$SNAP"
D="$(cd "$(dirname "$0")" && pwd)"
FILES="swissprot.pdb swissprot.phr swissprot.pin swissprot.pjs swissprot.pog swissprot.pos
swissprot.pot swissprot.ppd swissprot.ppi swissprot.psq swissprot.ptf swissprot.pto taxonomy4blast.sqlite3"
for f in $FILES; do
  aws s3api copy-object --copy-source "ncbi-blast-databases/$SNAP/$f" --bucket "$B" --key "$P/$f" \
    --checksum-algorithm SHA256 --region us-west-2 > /dev/null
  b64=$(aws s3api head-object --bucket "$B" --key "$P/$f" --checksum-mode ENABLED --region us-west-2 \
    --query ChecksumSHA256 --output text)
  got=$(printf '%s' "$b64" | base64 -d | od -An -tx1 | tr -d ' \n')
  if [ "${PIN:-1}" = 0 ]; then echo "$got  $f"; continue; fi
  want=$(awk -v f="$f" '$2==f{print $1}' "$D/inputs.sha256")
  [ "$got" = "$want" ] || { echo "FAIL: $f staged as $got, pinned $want"; exit 1; }
  echo "ok  $f  $got"
done
[ "${PIN:-1}" = 0 ] && exit 0
for f in inputs.sha256 run.sh diamond.sh; do
  aws s3 cp "$D/$f" "s3://$B/inputs/blast-real/$f" --region us-west-2 --only-show-errors
done
echo "staged s3://$B/$P/"
