#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: every image, design and time series is
# synthesised, and each reference is a closed form or an exact identity.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=nilearn does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
grep -q 'v = v if isinstance(v, str) else str(v)' identities.py \
  || { echo "  rec() does not str() its value" >&2; exit 1; }
grep -q 'signal_scaling=False' identities.py \
  || { echo "  the GLM must set signal_scaling=False, or effect sizes are in percent signal change" >&2; exit 1; }
grep -q 'cov_estimator=EmpiricalCovariance()' identities.py \
  || { echo "  the Pearson comparison must use EmpiricalCovariance; the default is Ledoit-Wolf" >&2; exit 1; }
echo "  identities.py parses; GLM unscaled, correlation empirical"

sha256sum identities.py > pins.sha256 2>/dev/null || shasum -a 256 identities.py > pins.sha256
aws s3 cp --region "$REGION" --only-show-errors identities.py "$BUCKET/inputs/nilearn/identities.py"
aws s3 cp --region "$REGION" --only-show-errors pins.sha256   "$BUCKET/inputs/nilearn/pins.sha256"
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/nilearn/  (no data staged -- everything is synthesised)"
