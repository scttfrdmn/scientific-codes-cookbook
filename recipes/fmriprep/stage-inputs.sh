#!/usr/bin/env bash
# Stage fMRIPrep's own CI inputs for its ds005 "fasttrack" run, plus YOUR FreeSurfer licence.
#
# The inputs are exactly what fMRIPrep's CI uses (.circleci/config.yml at 25.2.7): downsampled
# ds005 and precomputed FreeSurfer + sMRIPrep derivatives, from OSF project fvuh8. Each archive
# is checked to BE an archive before it is hashed -- OSF serves an HTML maintenance page with
# HTTP 200 -- and then against the sha256 pins below. CI's one edit to the data (adding
# "PhaseEncodingDirection": "j" to the BOLD sidecar) is applied here, the same way CI does it.
#
# The FreeSurfer licence is issued per registered user, so the recipe cannot ship one: point
# FS_LICENSE_FILE at yours (free: https://surfer.nmr.mgh.harvard.edu/registration.html). It
# goes to your private bucket only and is never printed.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=fmriprep does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LICENSE="${FS_LICENSE_FILE:-$SCRIPT_DIR/../../freesurfer-license.txt}"
[ -s "$LICENSE" ] || { echo "  no FreeSurfer licence at $LICENSE -- set FS_LICENSE_FILE" >&2; exit 1; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
OSF=https://files.osf.io/v1/resources/fvuh8/providers/osfstorage
fetch() {   # name osf-id kind sha256
  curl -fsSL --retry 5 -o "$1" "$OSF/$2"
  file -b "$1" | grep -q "$3" || { echo "  $1 is not a $3 archive ($(file -b "$1" | cut -c1-40)) -- is OSF up?" >&2; exit 1; }
  echo "$4  $1" | shasum -a 256 -c - >/dev/null || { echo "  $1 does not match its pin" >&2; exit 1; }
  echo "  $1: $3, matches its pin"
}
fetch ds005_downsampled.tar.gz         57f32a429ad5a101f977eb75 gzip PIN_DS005
fetch ds005_derivatives_freesurfer.zip 637bab2c92a98a1c0606e6a0 Zip  PIN_FREESURFER
fetch ds005_sub-01_smriprep.zip        637cf6505ea438007b0497d3 Zip  PIN_SMRIPREP

mkdir data deriv
tar -xzf ds005_downsampled.tar.gz -C data
unzip -q ds005_derivatives_freesurfer.zip -d deriv
unzip -q ds005_sub-01_smriprep.zip -d deriv
# CI's own edit (config.yml, "Inject pretend metadata"): the downsampled sidecar lacks PE direction
sc=data/ds005/task-mixedgamblestask_bold.json
grep -q PhaseEncodingDirection "$sc" || awk 'NR==1{print; print "    \"PhaseEncodingDirection\": \"j\","} NR!=1' "$sc" > sc.tmp && mv sc.tmp "$sc"
grep -q '"PhaseEncodingDirection": "j"' "$sc" || { echo "  could not apply CI's PhaseEncodingDirection edit" >&2; exit 1; }
echo "  extracted; CI's PhaseEncodingDirection edit applied"

aws s3 cp --region "$REGION" --only-show-errors --recursive data/ds005/          "$BUCKET/inputs/fmriprep/ds005/"
aws s3 cp --region "$REGION" --only-show-errors --recursive deriv/freesurfer/    "$BUCKET/inputs/fmriprep/freesurfer/"
aws s3 cp --region "$REGION" --only-show-errors --recursive deriv/smriprep/      "$BUCKET/inputs/fmriprep/smriprep/"
aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/legacy.yml" "$BUCKET/inputs/fmriprep/legacy.yml"
aws s3 cp --region "$REGION" --only-show-errors "$SCRIPT_DIR/ds005_fasttrack_outputs.txt" "$BUCKET/inputs/fmriprep/ds005_fasttrack_outputs.txt"
aws s3 cp --region "$REGION" --only-show-errors "$LICENSE" "$BUCKET/inputs/fmriprep/license.txt"
echo "  licence staged (contents not shown)"
echo "done."
echo "  $BUCKET/inputs/fmriprep/  (ds005 + FreeSurfer/sMRIPrep derivatives from fMRIPrep's CI, and your licence)"
