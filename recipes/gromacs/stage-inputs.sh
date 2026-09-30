#!/usr/bin/env bash
# Stage benchMEM, the standard GROMACS benchmark system: 81,743 atoms, PME, Berendsen NPT,
# 10,000 steps. A .tpr is self-contained, so this is the only input the run needs.
#
# Published by the Dept. of Theoretical and Computational Biophysics, Max Planck Institute for
# Multidisciplinary Sciences, Göttingen, under CC-BY 4.0 — the set used in Kutzner et al.
# (doi:10.1002/jcc.24030), which is what makes ns/day here comparable to published numbers.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=NAME does this}"
REGION="${AWS_REGION:-us-west-2}"
DST="s3://$BUCKET/inputs/gromacs/benchMEM.tpr"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

if aws s3 ls "$DST" --region "$REGION" >/dev/null 2>&1; then
  echo "benchMEM already staged: $DST"
  exit 0
fi

cd "$WORK"
# The download is served as a ZIP despite the .tpr-looking URL; both hashes are pinned.
curl -fsSL --max-time 300 -o benchMEM.zip https://www.mpinat.mpg.de/benchMEM
echo "3c1c8cd4f274d532f48c4668e1490d389486850d6b3b258dfad4581aa11380a4  benchMEM.zip" > z.sha256
sha256sum -c z.sha256 2>/dev/null || shasum -a 256 -c z.sha256
unzip -o -q benchMEM.zip
echo "5099268bf3a3d948c03b3b78432f29a2c5d4207b54d02e7e294f8138b587d473  benchMEM.tpr" > t.sha256
sha256sum -c t.sha256 2>/dev/null || shasum -a 256 -c t.sha256

aws s3 cp benchMEM.tpr "$DST" --region "$REGION" --only-show-errors
echo "staged $DST   (81,743 atoms, PME, NPT, 10,000 steps = 20 ps)"
