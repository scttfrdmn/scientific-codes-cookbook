#!/usr/bin/env bash
# Stage rdkit-real: the whole ChEMBL 37 chemreps release (chembl_id, canonical_smiles,
# standard_inchi, standard_inchi_key; 280 MB gzipped) plus the two scripts the task runs.
#
# Tier: a durable public id. releases/chembl_37/ on EBI's FTP is immutable, and EBI publishes the
# file's sha256 in that directory's checksums.txt. The file is fetched ON AWS by 00-fetch.task.json
# (nothing passes through this machine) and checked there against EBI's sha256; this script checks
# the kept copy against pins.sha256, which the real task checks again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/rdkit-real"

if ! aws s3 ls --region "$REGION" "$DST/chembl37/chembl_37_chemreps.txt.gz" >/dev/null 2>&1; then
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" rdkit arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/chembl37/data.sha256" - | awk '{print $1"  chembl37/"$2}')"
want="$(grep ' chembl37/' "$HERE/pins.sha256")"
[ "$got" = "$want" ] || { printf 'FAIL: staged data is\n%s\npinned\n%s\n' "$got" "$want" >&2; exit 1; }
echo "data: matches pins.sha256"
( cd "$HERE" && grep -E ' real\.(py|sh)$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
