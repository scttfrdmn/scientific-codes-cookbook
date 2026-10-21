#!/usr/bin/env bash
# Stage nf-spawn-real's scripts. The data is not copied: the workload is mafft-real's staged
# PF01255.25 (Pfam 38.2 seed, 919 sequences, gaps removed), read in place and checked against its
# pin here and in every direct task.
#   bash measurements/nf-spawn-real/stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HOME/.xtmp/nf-spawn-real-stage"
[ -d "$HOME/.xtmp" ] || WORK="$(mktemp -d)"
mkdir -p "$WORK"; trap 'rm -rf "$WORK"' EXIT

got=$(aws s3 cp --region "$REGION" --only-show-errors "s3://$B/inputs/mafft-real/pf01255.25_unaligned.fa" - | shasum -a 256 | cut -d' ' -f1)
grep -q "^$got  pf01255.25_unaligned.fa$" "$HERE/pins.sha256" || { echo "FAIL: staged PF01255.25 is $got" >&2; exit 1; }
python3 -I "$HERE/render.py" "$WORK"          # main.nf's five process scripts, as the engine renders them
cp "$HERE/direct.sh" "$WORK/"
(cd "$WORK" && grep -v ' pf01255.25_unaligned.fa$' "$HERE/pins.sha256" | shasum -a 256 -c)
for f in direct.sh mafft.sh muscle.sh tree-mafft.sh tree-muscle.sh rf.sh; do
  aws s3 cp --region "$REGION" --only-show-errors "$WORK/$f" "s3://$B/inputs/nf-spawn-real/$f"
done
aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.sha256" "s3://$B/inputs/nf-spawn-real/pins.sha256"
echo "staged to s3://$B/inputs/nf-spawn-real/"
