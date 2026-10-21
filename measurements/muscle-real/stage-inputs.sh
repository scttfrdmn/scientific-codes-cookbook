#!/usr/bin/env bash
# Stage muscle-real's task script and pins. The data is NOT copied: muscle reads mafft-real's staged
# bytes (Pfam 38.2 PF01255.25, the whole 919-sequence seed, gaps removed, plus Pfam's curated seed
# alignment as the reference) and mafft-real's reproducible L-INS-i alignment of them, so both
# aligners are scored on identical bytes. pins.sha256 pins all four files the task reads; the task
# checks them before it aligns anything.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
check() {   # <s3 url> <name in pins.sha256>
  want=$(awk -v f="$2" '$2==f{print $1}' "$HERE/pins.sha256")
  got=$(aws s3 cp --region "$REGION" --only-show-errors "$1" - | shasum -a 256 | cut -d' ' -f1)
  [ -n "$want" ] && [ "$got" = "$want" ] || { echo "FAIL: $1 is $got, pin says $want" >&2; exit 1; }
  echo "$2 == pin ($1)"
}
check "$BUCKET/inputs/mafft-real/pf01255.25_unaligned.fa" pf01255.25_unaligned.fa
check "$BUCKET/inputs/mafft-real/pf01255.25_ref.fa" pf01255.25_ref.fa
for p in runs runs-x86_64 runs-amd; do    # each chip's spec reads its own chip's mafft run; all three are one file
  check "$BUCKET/$p/mafft-real/pf01255/linsi.fa" mafft_linsi.fa
done
[ "$(shasum -a 256 "$HERE/real.sh" | cut -d' ' -f1)" = "$(awk '$2=="real.sh"{print $1}' "$HERE/pins.sha256")" ] \
  || { echo "FAIL: real.sh differs from its pin" >&2; exit 1; }
aws s3 cp --region "$REGION" --only-show-errors "$HERE/real.sh" "$BUCKET/inputs/muscle-real/real.sh"
aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.sha256" "$BUCKET/inputs/muscle-real/pins.sha256"
echo "staged to $BUCKET/inputs/muscle-real/"
