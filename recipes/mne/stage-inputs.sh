#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: mne-base ships neither its tests nor its sample
# data (both come down through pooch at run time), so every signal is synthesised in-run and
# each reference is a planted answer or an exact identity.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=mne does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
grep -q 'v = v if isinstance(v, str) else str(v)' identities.py \
  || { echo "  rec() does not str() its value" >&2; exit 1; }
grep -q 'random_state=0' identities.py \
  || { echo "  ICA must be seeded, or its components come back in a different order each run" >&2; exit 1; }
echo "  identities.py parses; ICA seeded"

sha256sum identities.py > pins.sha256 2>/dev/null || shasum -a 256 identities.py > pins.sha256
aws s3 cp --region "$REGION" --only-show-errors identities.py "$BUCKET/inputs/mne/identities.py"
aws s3 cp --region "$REGION" --only-show-errors pins.sha256   "$BUCKET/inputs/mne/pins.sha256"
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/mne/  (no data staged -- everything is synthesised)"
