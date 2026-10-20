#!/usr/bin/env bash
# Stage measurements/mdtraj-real: the whole Folding@home FAST run of SARS-CoV-2 Mpro (RODA), the
# DHFR DCD that openmm-mdanalysis-real wrote and checked, and this directory's scripts.
#
# Tier: RODA. s3://fah-public-data-covid19-cryptic-pockets (Folding@home, Zimmerman et al. 2021,
# Nat. Chem. 13:651), SARS-CoV-2/nsp5_monomer/FAST_simulations/ (60 XTCs) and input_files/ (the
# topology). Copied server-side into this bucket -- nothing passes through this machine -- and
# pinned by the sha256s probe.task.json recorded on AWS (inputs.sha256), re-checked in every task.
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="s3://fah-public-data-covid19-cryptic-pockets/SARS-CoV-2/nsp5_monomer"
DST="s3://$BUCKET/inputs/mdtraj-real"
for d in FAST_simulations input_files; do
  aws s3 cp --recursive --only-show-errors --source-region us-east-1 --region "$REGION" \
    "$SRC/$d/" "$DST/fah-nsp5-fast/$d/"
done
# openmm-mdanalysis-real's graviton4 run wrote this DCD and recorded MDAnalysis's reading of it;
# the bytes are copied under inputs/ so every chip reads the same file (spec_arch.py would send an
# x86 task to the x86 run's /runs-x86_64/ copy, a different trajectory).
for f in traj-JAC.dcd constraints-JAC.npz; do
  aws s3 cp --only-show-errors --region "$REGION" "s3://$BUCKET/runs/openmm-mdanalysis-real/r1/$f" "$DST/jac/$f"
done
for f in run.sh real.py check.py $( [ -f "$HERE/inputs.sha256" ] && echo inputs.sha256 ); do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged $DST/"
