#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: the design, the planted coefficients and both
# responses are synthesised in the task from a fixed seed, and every reference is a closed form,
# an optimality condition, or base R's lm()/glm().
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=glmnet does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.R" .
test -s identities.R || { echo "  identities.R is empty" >&2; exit 1; }
grep -q 'GLMNET IDENTITIES OK' identities.R \
  || { echo "  identities.R has no completion sentinel" >&2; exit 1; }
grep -q 'foldid = foldid' identities.R \
  || { echo "  cv.glmnet must take a fixed foldid, or the selected model is not reproducible" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.R "$(wc -c < identities.R | tr -d '[:space:]')"

sha256sum identities.R > pins.sha256 2>/dev/null || shasum -a 256 identities.R > pins.sha256
aws s3 cp --region "$REGION" --only-show-errors identities.R "$BUCKET/inputs/glmnet/identities.R"
aws s3 cp --region "$REGION" --only-show-errors pins.sha256  "$BUCKET/inputs/glmnet/pins.sha256"
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/glmnet/  (no data staged -- everything is synthesised)"
