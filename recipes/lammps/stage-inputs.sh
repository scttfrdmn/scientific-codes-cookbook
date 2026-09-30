#!/usr/bin/env bash
# Stage the rhodopsin system for LAMMPS' own biomolecular benchmark: 32,000 atoms, CHARMM,
# which the recipe replicates to 128,000 at run time.
#
# The conda package ships binaries but not the bench/ tree, so the system comes from the LAMMPS
# repo at the tag matching the packaged version -- a deck or data file from another release is a
# different workload. The input DECK is not staged: it lives inline in the task spec, so its two
# deviations from upstream in.rhodo are visible in git diff.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
REGION="${AWS_REGION:-us-west-2}"
TAG=patch_22Jul2025
DST="s3://$BUCKET/inputs/lammps/data.rhodo"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

if aws s3 ls "$DST" --region "$REGION" >/dev/null 2>&1; then
  echo "data.rhodo already staged: $DST"
  exit 0
fi

cd "$WORK"
curl -fsSL --max-time 300 -o data.rhodo \
  "https://raw.githubusercontent.com/lammps/lammps/$TAG/bench/data.rhodo"
echo "9b14e259b99b8a28ebbfd86715524c415f82cd5912c164c3827991ac32f23863  data.rhodo" > d.sha256
sha256sum -c d.sha256 2>/dev/null || shasum -a 256 -c d.sha256

# Fetched and hashed for provenance only -- the run uses the inline deck, which is this file
# plus `replicate 2 2 1` and `run 1000`.
curl -fsSL --max-time 120 -o in.rhodo \
  "https://raw.githubusercontent.com/lammps/lammps/$TAG/bench/in.rhodo"
echo "5599f0388a36c9412a34c1ce892dcc3e6880c7acee3fcd3a96b6fbde7531ad2d  in.rhodo" > i.sha256
sha256sum -c i.sha256 2>/dev/null || shasum -a 256 -c i.sha256

aws s3 cp data.rhodo "$DST" --region "$REGION" --only-show-errors
echo "staged $DST   (32,000 atoms; replicated to 128,000 at run time)"
