#!/usr/bin/env bash
# Stage fMRIPrep's own CI inputs for its ds005 "fasttrack" run, plus YOUR FreeSurfer licence.
#
# The inputs are exactly what fMRIPrep's CI uses (.circleci/config.yml at 25.2.7): downsampled
# ds005 and precomputed FreeSurfer + sMRIPrep derivatives, from OSF project fvuh8. They are fetched
# ON AWS by a small staging task (stage.spec.json + fetch-osf.py), which checks each download is an
# archive and matches OSF's published sha256 before extracting it and applying CI's one data edit.
#
# The FreeSurfer licence is issued per registered user, so the recipe cannot ship one: point
# FS_LICENSE_FILE at yours (free: https://surfer.nmr.mgh.harvard.edu/registration.html). It is
# copied straight to your private bucket and never printed.
set -euo pipefail

BUCKET="${1:?pass your bucket -- make stage RECIPE=fmriprep does this}"
REGION="${AWS_REGION:-us-west-2}"
D="$(cd "$(dirname "$0")" && pwd)"
LICENSE="${FS_LICENSE_FILE:?set FS_LICENSE_FILE to your FreeSurfer licence file}"
[ -s "$LICENSE" ] || { echo "  no FreeSurfer licence at $LICENSE" >&2; exit 1; }
cp_() { aws s3 cp --region "$REGION" --only-show-errors "$1" "s3://$BUCKET/inputs/fmriprep/$2"; }

cp_ "$LICENSE" license.txt && echo "  licence staged (contents not shown)"
for f in legacy.yml ds005_fasttrack_outputs.txt check-outputs.sh fetch-osf.py; do cp_ "$D/$f" "$f"; done

if aws s3 ls "s3://$BUCKET/inputs/fmriprep/stage.ok" --region "$REGION" >/dev/null 2>&1; then
  echo "  OSF inputs already staged"
else
  echo "  fetching the OSF inputs on AWS (c8g.large, ~5 min, self-terminating)..."
  sha=$(shasum -a 256 "$D/fetch-osf.py" 2>/dev/null || sha256sum "$D/fetch-osf.py"); sha=${sha%% *}
  tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
  sed -e "s|\${COOKBOOK_BUCKET}|$BUCKET|g" -e "s|\${FETCH_SHA}|$sha|" \
      -e "s|\"cookbook-fmriprep-stage\"|\"cookbook-fmriprep-stage-$(date +%Y%m%d%H%M%S)\"|" "$D/stage.spec.json" > "$tmp"
  spawn task run --spec "$tmp" --region "$REGION" --wait
  aws s3 ls "s3://$BUCKET/inputs/fmriprep/stage.ok" --region "$REGION" >/dev/null \
    || { echo "  staging task did not finish -- see its command.log" >&2; exit 1; }
fi
echo "done: s3://$BUCKET/inputs/fmriprep/  (ds005 + FreeSurfer/sMRIPrep derivatives from fMRIPrep's CI, and your licence)"
