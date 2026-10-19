#!/usr/bin/env bash
# Stage vina-real: the PoseBusters paper's complexes (Zenodo 8278563), prepared once into Vina inputs.
#
# Tier: a durable public id. Zenodo record 8278563 (DOI 10.5281/zenodo.8278563) is immutable and
# publishes each file's md5. 00-fetch.task.json downloads the archive ON AWS, checks it against that
# md5 and records its sha256 (nothing passes through this machine). 01-prep.task.json turns it into
# receptor/ligand PDBQTs on one chip, so every chip docks the same bytes. pins.sha256 pins all of it.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/vina-real"
spec() { python3 "$ROOT/scripts/spec_arch.py" vina arm64 "$BUCKET" "$(date +%s)" "$HERE/$1"; }

for f in prep.py dock.py real.sh posebusters_308_ids.txt pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
( cd "$HERE" && grep -E ' (prep\.py|dock\.py|real\.sh|posebusters_308_ids\.txt)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )

aws s3 ls --region "$REGION" "$DST/posebusters/posebusters_paper_data.zip" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(spec 00-fetch.task.json)"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/posebusters/data.sha256" - | awk '{print $1"  data/"$2}')"
want="$(grep -E ' data/posebusters_paper_(data\.zip|results\.csv)$' "$HERE/pins.sha256")"
[ "$got" = "$want" ] || { printf 'FAIL: staged archive is\n%s\npinned\n%s\n' "$got" "$want" >&2; exit 1; }

aws s3 ls --region "$REGION" "$DST/prep/prep.tar" >/dev/null 2>&1 \
  || spawn task run --wait --spec "$(spec 01-prep.task.json)"
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/prep/prep.sha256" - | awk '{print $1"  data/"$2}')"
want="$(grep ' data/prep.tar$' "$HERE/pins.sha256")"
# Open Babel places some receptor polar hydrogens differently on every run, so a fresh prep.tar never
# matches this pin: the pinned one is the input the measurement docked. A new prep is a new input.
[ "$got" = "$want" ] || { printf 'FAIL: staged prep is\n%s\npinned\n%s\n(a re-run prep differs by construction; re-pin it as your own input)\n' "$got" "$want" >&2; exit 1; }
echo "staged to $DST/"
