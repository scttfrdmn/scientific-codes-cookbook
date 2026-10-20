#!/usr/bin/env bash
# Stage foldseek-real: the AlphaFold DB v6 human proteome (UP000005640, 23,586 models), as EMBL-EBI
# distributes it -- ftp.ebi.ac.uk/pub/databases/alphafold/v6/UP000005640_9606_HUMAN_v6.tar, CC BY 4.0
# -- plus the task script.
#
# Tier: a versioned public release (AFDB v6, UniProt 2025_03). The 5.18 GB archive is fetched ON AWS
# by 00-fetch.task.json -- nothing passes through this machine -- and kept only if its size and its
# mmCIF count equal what EBI publishes in download_metadata.json. EBI publishes no checksum, so the
# sha256 the first fetch recorded is the pin: the fetch writes it, it must equal pins.sha256 here,
# and the real task checks pins.sha256 again at stage-in.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DST="s3://$BUCKET/inputs/foldseek-real"
TAR=UP000005640_9606_HUMAN_v6.tar

aws s3 cp --region "$REGION" --only-show-errors "$HERE/fetch.py" "$DST/fetch.py"
if ! aws s3 ls --region "$REGION" "$DST/data/data.sha256" >/dev/null 2>&1; then
  # the fetch image is aarch.science's single-cell env, only for its python3; any arm64 python works
  spawn task run --wait --spec "$(python3 "$ROOT/scripts/spec_arch.py" scvelo arm64 "$BUCKET" "$(date +%s)" "$HERE/00-fetch.task.json")"
fi
got="$(aws s3 cp --region "$REGION" --only-show-errors "$DST/data/data.sha256" -)"
want="$(grep " $TAR\$" "$HERE/pins.sha256")"
[ "$got" = "$want" ] || { echo "FAIL: the staged archive does not match pins.sha256" >&2; exit 1; }
echo "data: $TAR matches pins.sha256"
( cd "$HERE" && grep -E ' real\.sh$' pins.sha256 | { sha256sum -c 2>/dev/null || shasum -a 256 -c; } )
for f in real.sh pins.sha256 compare.sh compare.sha256 diag.sh diag.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
# the diagnostic's input: the pairs one build finds and the other does not (from compare.task.json)
aws s3 cp --region "$REGION" --only-show-errors "$HERE/results/disagreeing_pairs.tsv" "$DST/disagreeing_pairs.tsv"
echo "staged to $DST/"
