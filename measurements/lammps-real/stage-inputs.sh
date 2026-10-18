#!/usr/bin/env bash
# Stage measurements/lammps-real: upstream's scaled rhodopsin deck (bench/in.rhodo.scaled at
# patch_22Jul2025, the tag matching the packaged LAMMPS), plus this directory's scripts.
# The 32,000-atom system itself, data.rhodo, is the recipe's own staged copy
# (recipes/lammps/stage-inputs.sh) and is reused, not copied a second time.
#   stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
TAG=patch_22Jul2025
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
aws s3 ls "s3://$B/inputs/lammps/data.rhodo" --region "$REGION" >/dev/null \
  || { echo "data.rhodo is not staged: run make stage RECIPE=lammps first"; exit 1; }
curl -fsSL --max-time 120 -o "$WORK/in.rhodo.scaled" \
  "https://raw.githubusercontent.com/lammps/lammps/$TAG/bench/in.rhodo.scaled"
grep in.rhodo.scaled "$D/inputs.sha256" > "$WORK/deck.sha256"
( cd "$WORK" && { sha256sum -c deck.sha256 2>/dev/null || shasum -a 256 -c deck.sha256; } )
aws s3 cp "$WORK/in.rhodo.scaled" "s3://$B/inputs/lammps-real/in.rhodo.scaled" --region "$REGION" --only-show-errors
for f in inputs.sha256 run.sh check.py; do
  aws s3 cp "$D/$f" "s3://$B/inputs/lammps-real/$f" --region "$REGION" --only-show-errors
done
echo "staged s3://$B/inputs/lammps-real/ (deck, scripts, pins); data.rhodo reused from inputs/lammps/"
