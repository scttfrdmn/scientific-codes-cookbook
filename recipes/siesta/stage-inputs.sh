#!/usr/bin/env bash
# Stage SIESTA's Si pseudopotential to S3, once, pinned by digest.
#
# conda-forge's siesta ships NO pseudopotentials, so a real SCF needs one staged.
# The canonical, pinnable source is SIESTA's own test suite at the tag that matches
# the container's version (5.4.2): Tests/Pseudos/Si.psf. Using the version-matched
# pseudopotential is what lets the recipe cross-check its total energy against
# SIESTA's committed reference output for the same test (Total = -214.377236 eV).
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=NAME does this}"
SRC="https://gitlab.com/siesta-project/siesta/-/raw/5.4.2/Tests/Pseudos/Si.psf"
SHA="0afddde32f30e43fa8d603822f3dd1ddf357e8ff33a1af21eb4982b6e63080d7"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

echo "== fetch Si.psf from the SIESTA 5.4.2 tag =="
curl -fsSL "$SRC" -o Si.psf

echo "== verify against the pinned sha256 =="
printf '%s  %s\n' "$SHA" Si.psf > pins.sha256
shasum -a 256 -c pins.sha256

echo "== sanity: it is a Troullier-Martins .psf for Si =="
head -1 Si.psf | grep -q ' Si ' || { echo "not a Si pseudopotential"; exit 1; }

echo "== upload to $BUCKET/inputs/siesta/ =="
aws s3 cp Si.psf "$BUCKET/inputs/siesta/Si.psf"
echo "done."
