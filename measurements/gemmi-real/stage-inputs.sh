#!/usr/bin/env bash
# Stage gemmi-real: the manifest of the wwPDB archive's 20260101 RODA snapshot, plus the scripts.
#
# Tier: RODA (s3://pdbsnapshots, the RCSB's dated, frozen yearly snapshots; us-west-2). Nothing is
# copied: the task streams every object from the public bucket and checks it against the manifest's
# size and S3 ETag before gemmi reads it. The manifest itself is rebuilt here from three listings
# (metadata only, ~70 MB) and must reproduce the pinned sha256 -- the snapshot is frozen, so it does.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/gemmi-real"
SNAP=20260101
W="$(mktemp -d "${TMPDIR:-/tmp}/gemmi-real.XXXXXX")"; trap 'rm -rf "$W"' EXIT

for t in mmCIF pdb structure_factors; do
  aws s3api list-objects-v2 --no-sign-request --region us-west-2 --bucket pdbsnapshots \
    --prefix "$SNAP/pub/pdb/data/structures/divided/$t/" \
    --query 'Contents[].[Key,Size,ETag]' --output text > "$W/etag-$t.txt" &
done
wait
python3 -I "$HERE/build_manifest.py" "$W/etag-mmCIF.txt" "$W/etag-pdb.txt" "$W/etag-structure_factors.txt" > "$W/manifest.tsv"
gzip -n -c "$W/manifest.tsv" > "$W/manifest.tsv.gz"
cp "$HERE/real.py" "$HERE/real.sh" "$HERE/pins.sha256" "$W/"
( cd "$W" && { sha256sum -c pins.sha256 2>/dev/null || shasum -a 256 -c pins.sha256; } ) \
  || { echo "FAIL: the manifest or a script does not match pins.sha256" >&2; exit 1; }
for f in manifest.tsv.gz real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$W/$f" "$DST/$f"
done
echo "staged to $DST/"
