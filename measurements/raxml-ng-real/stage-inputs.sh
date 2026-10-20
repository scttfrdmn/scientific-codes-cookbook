#!/usr/bin/env bash
# Stage raxml-ng-real: the task scripts, plus pins for the inputs they read.
#
#   bash stage-inputs.sh <bucket>
#
# The alignment is not staged here. It is iqtree-real's PF01255.25 seed alignment (Pfam 38.2,
# 919 x 533), already in the bucket under inputs/iqtree-real/ (measurements/iqtree-real/
# stage-inputs.sh: EBI's md5 on Pfam-A.seed.gz, then our sha256), and both tools read that one
# object: the cross-check only means something on identical bytes. eval.sh is iqtree-real's
# fixed-point loop, the same pinned file. This script checks both against pins.sha256 in place.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/raxml-ng-real"
sha() { if command -v sha256sum >/dev/null; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }
pin() { awk -v f="$1" '$2==f{print $1}' "$HERE/pins.sha256"; }
s3get() { aws s3 cp --region "$REGION" --only-show-errors "$1" -; }

for f in pf01255.25_seed.fa:inputs/iqtree-real pfam38.2_seed_alignment.fa:inputs/iqtree eval.sh:inputs/iqtree-real; do
  name=${f%%:*} at=${f#*:}
  got=$(s3get "$BUCKET/$at/$name" | sha)
  [ "$got" = "$(pin "$name")" ] || { echo "FAIL: $at/$name is $got, pinned $(pin "$name")" >&2; exit 1; }
  echo "$at/$name  $got  == pin"
done
aws s3 cp --region "$REGION" --only-show-errors "$BUCKET/inputs/iqtree-real/eval.sh" "$PREFIX/eval.sh"
for f in real.sh iq.sh; do
  [ "$(sha < "$HERE/$f")" = "$(pin "$f")" ] || { echo "FAIL: $f does not match its pin" >&2; exit 1; }
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$PREFIX/$f"
done
aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.sha256" "$PREFIX/pins.sha256"
cat "$HERE/pins.sha256"
echo "staged to $PREFIX/"
