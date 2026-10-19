#!/usr/bin/env bash
# Stage cp2k-real: CP2K's own 64-water benchmark inputs and the reference outputs its developers
# committed beside them, from tag v2026.2 (the version in the image), plus the task's scripts.
#
# Tier: a durable public id, a versioned release. Each file is fetched at the tag and kept only if
# its git blob id equals the one in the v2026.2 tree (manifest.tsv); pins.sha256 then pins the
# bytes and the task checks them again at stage-in. 14 text files, 113 KB.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/cp2k-real"
RAW=https://raw.githubusercontent.com/cp2k/cp2k/v2026.2

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/data"
grep -v '^#' "$HERE/manifest.tsv" | while IFS=$'\t' read -r path blob size; do
  curl -fsSL -o "$tmp/data/$(basename "$path")" "$RAW/$path"
  python3 -I -c '
import hashlib, sys
b = open(sys.argv[1], "rb").read()
got = hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()
sys.exit(0 if got == sys.argv[2] else "blob mismatch: %s %s != %s" % (sys.argv[1], got, sys.argv[2]))
' "$tmp/data/$(basename "$path")" "$blob"
done
echo "data: $(ls "$tmp/data" | wc -l | tr -d ' ') files match their v2026.2 blob ids"

( cd "$tmp" && shasum -a 256 data/* ) > "$tmp/data.sha256"
( cd "$HERE" && shasum -a 256 real.py real.sh ) > "$tmp/scripts.sha256"
cat "$tmp/data.sha256" "$tmp/scripts.sha256" > "$tmp/pins.sha256"
if [ -f "$HERE/pins.sha256" ] && ! cmp -s "$tmp/pins.sha256" "$HERE/pins.sha256"; then
  diff "$HERE/pins.sha256" "$tmp/pins.sha256" || true
  echo "pins.sha256 changed -- review and commit it (a changed script means a re-run)" >&2
fi
cp "$tmp/pins.sha256" "$HERE/pins.sha256"
aws s3 cp --region "$REGION" --only-show-errors --recursive "$tmp/data/" "$DST/data/"
for f in real.py real.sh; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
aws s3 cp --region "$REGION" --only-show-errors "$HERE/pins.sha256" "$DST/pins.sha256"
echo "staged to $DST/"
