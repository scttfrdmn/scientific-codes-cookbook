#!/usr/bin/env bash
# Stage specutils-real: DESI DR1 (iron) healpix group 100 of the main-survey dark program --
# all 100 nside-64 pixels 10000-10099, each a coadd (the spectra) and DESI's own emission-line
# catalogue of it (emline-*.fits, desispec's emlinefit afterburner) -- from the RODA bucket
# s3://desidata (us-west-2, Payer: BucketOwner), plus the two DESI source files that define how the
# catalogue was computed, at pinned commits.
#
# Pinned by inputs.tsv: each object's sha256 as published by DESI in the per-pixel
# redux_iron_healpix_main_dark_100_<pix>.sha256sum files. desidata is not versioned, so the
# content hash is the pin. The copy is server-side (CopyObject, no bytes through this machine) and
# S3 computes the full-object SHA256 of what it wrote; a mismatch aborts. The task re-checks every
# staged byte against pins.sha256 before it runs.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail

BUCKET="${1:?usage: stage-inputs.sh <bucket>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="inputs/specutils-real"

while IFS=$'\t' read -r name key sha; do
  got=$(aws s3api copy-object --region us-west-2 --bucket "$BUCKET" --key "$PREFIX/desi/$name" \
          --copy-source "desidata/$key" --checksum-algorithm SHA256 \
          --query CopyObjectResult.ChecksumSHA256 --output text | base64 -d | xxd -p -c 64)
  [ "$got" = "$sha" ] || { echo "FAIL: $name sha256 $got, pinned $sha" >&2; exit 1; }
done < "$HERE/inputs.tsv"
echo "staged $(wc -l < "$HERE/inputs.tsv") DESI objects"

# The catalogue's definition: desispec emlinefit.py as of iron's emline run (2023-01-30; the file is
# unchanged from 3d40d6d043, 2022-10-21, to 952a9a29d0, 2023-01-25, bar docstrings), and the
# O'Donnell extinction curve it applies, desiutil dust.py at 8b51cfbaffcb (2022-08-29).
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
curl -fsSL -o "$T/desispec_emlinefit.py" https://raw.githubusercontent.com/desihub/desispec/952a9a29d044b8f4d9b1dbb6edbe402a6e741a97/py/desispec/emlinefit.py
curl -fsSL -o "$T/desiutil_dust.py" https://raw.githubusercontent.com/desihub/desiutil/8b51cfbaffcb6e65b2a98d074a6d53e27508e27e/py/desiutil/dust.py
( cd "$T" && printf '%s\n' \
  "ed51e9c567fdd3061e14889a6ff6c453c0bb2e0a442310f567f5122f14556678  desispec_emlinefit.py" \
  "4649129279f59732bb74174bc2202369ffc9bbf10b52fa65e1c158746d1c4ddf  desiutil_dust.py" | shasum -a 256 -c - )
for f in desispec_emlinefit.py desiutil_dust.py; do
  aws s3 cp --region us-west-2 --only-show-errors "$T/$f" "s3://$BUCKET/$PREFIX/$f"
done
for f in run.sh real.py check.py compare.py desiutil_log.py pins.sha256; do
  aws s3 cp --region us-west-2 --only-show-errors "$HERE/$f" "s3://$BUCKET/$PREFIX/$f"
done
echo "staged DESI sources + scripts to s3://$BUCKET/$PREFIX/"
