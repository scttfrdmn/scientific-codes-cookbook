#!/usr/bin/env bash
# Stage measurements/ase-phonopy-real: phonopy's own committed Si examples at v4.4.0 (the tag
# matching the packaged phonopy) -- the displaced supercells and the QE and VASP forces computed
# on them -- plus this directory's scripts. ~140 KB in all.
#   stage-inputs.sh <bucket>
set -euo pipefail
B="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
U=https://raw.githubusercontent.com/phonopy/phonopy/v4.4.0/example
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
for f in Si-QE/phonopy_disp.yaml Si-QE/supercell-001.out Si/phonopy_disp.yaml Si/vasprun.xml; do
  curl -fsSL --max-time 120 -o "$WORK/$(echo "$f" | tr / _)" "$U/$f"
done
( cd "$WORK" && { sha256sum -c "$D/inputs.sha256" 2>/dev/null || shasum -a 256 -c "$D/inputs.sha256"; } )
for f in "$WORK"/*; do
  aws s3 cp "$f" "s3://$B/inputs/ase-phonopy-real/$(basename "$f")" --region "$REGION" --only-show-errors
done
for f in inputs.sha256 run.sh forces.py analyse.py; do
  aws s3 cp "$D/$f" "s3://$B/inputs/ase-phonopy-real/$f" --region "$REGION" --only-show-errors
done
echo "staged s3://$B/inputs/ase-phonopy-real/"
