#!/usr/bin/env bash
# Stage the HGB divergence reproductions. covtype.data.gz comes from ../stage-inputs.sh (run it first).
#   measurements/scikit-learn-real/hgb-repro/stage.sh <bucket>
set -euo pipefail
BUCKET="s3://${1:?pass your bucket, e.g. \$(make -s print-bucket)}"
DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="614360d0257557dd1792834a85a1cdebfadc3c4f30b011d56afee7ffb5b15771"
sum256() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$DIR/hgb_repro.py" "$DIR/lgb_count.py" "$tmp/"
printf '%s  covtype.data.gz\n%s  hgb_repro.py\n' "$DATA" "$(sum256 "$tmp/hgb_repro.py")" > "$tmp/repro-pins.sha256"
printf '%s  covtype.data.gz\n%s  lgb_count.py\n' "$DATA" "$(sum256 "$tmp/lgb_count.py")" > "$tmp/count-pins.sha256"
aws s3 cp --only-show-errors --recursive "$tmp/" "$BUCKET/inputs/scikit-learn-real/hgb-repro/"
echo "staged to $BUCKET/inputs/scikit-learn-real/hgb-repro/"
