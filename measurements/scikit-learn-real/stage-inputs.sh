#!/usr/bin/env bash
# Stage UCI Covertype (all 581,012 rows) and the workload script.
#
# The bytes are pinned by scikit-learn 1.9.1's OWN pin: sklearn/datasets/_covtype.py fetches
# covtype.data.gz from figshare file 5976039 with this sha256. The UCI archive's copy
# (doi:10.24432/C50K5N, archive.ics.uci.edu/static/public/31/covertype.zip) is the same file,
# byte for byte -- checked when this was written; the script uses the figshare URL sklearn uses.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUCKET/inputs/scikit-learn-real"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

curl -sSfL -o covtype.data.gz https://ndownloader.figshare.com/files/5976039
cp "$SCRIPT_DIR/real.py" .
cat > pins.sha256 <<'PINS'
614360d0257557dd1792834a85a1cdebfadc3c4f30b011d56afee7ffb5b15771  covtype.data.gz
PINS
shasum -a 256 real.py >> pins.sha256
shasum -a 256 -c pins.sha256

for f in covtype.data.gz real.py pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$f" "$PREFIX/$f"
done
cat pins.sha256
echo "staged to $PREFIX/"
