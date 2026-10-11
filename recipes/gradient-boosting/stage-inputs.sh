#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: every dataset is generated from a fixed seed, and every
# reference is a structural guarantee rather than a value. What this script verifies locally is
# that the FIXTURES actually test what the assertions claim -- a flat unconstrained fit or a
# non-separable "separable" dataset would make the checks pass vacuously.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=gradient-boosting does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the fixtures must be able to fail, or the assertions are vacuous =="
python3 - <<'PY'
import sys
import numpy as np

SEED = 20261011
rng = np.random.default_rng(SEED)

# Same draw order as the recipe, so these are the actual fixtures.
Xc = rng.normal(size=(400, 3)); _ = np.full(400, 7.25)
Xs = rng.normal(size=(500, 4)); ys = (Xs[:, 0] > 0.5).astype(int)
Xr = rng.normal(size=(300, 5)); _ = Xr[:, 0]*3 - Xr[:, 1]*2 + rng.normal(scale=0.1, size=300)
Xm = rng.uniform(0, 1, size=(600, 2)); ym = -4.0*Xm[:, 0] + rng.normal(scale=0.05, size=600)
n = 1200
Xi = rng.normal(size=(n, 6)); yi = 5.0*Xi[:, 0] + 0.5*Xi[:, 1] + rng.normal(scale=0.1, size=n)

# 1. the "separable" set must actually be separable by a single threshold, and not degenerate:
#    if one class were empty, zero errors would be trivial.
frac = ys.mean()
print("  separable set: %d positives of %d (%.1f%%)" % (ys.sum(), len(ys), 100*frac))
if not (0.1 < frac < 0.9):
    sys.exit("  the classes are too imbalanced for 0 errors to mean anything")
if not ((Xs[:, 0] > 0.5) == ys.astype(bool)).all():
    sys.exit("  the labels are not a pure threshold on x0")

# 2. the monotone fixture must DEcrease in x0, or a non-decreasing constraint costs nothing.
slope = np.polyfit(Xm[:, 0], ym, 1)[0]
print("  monotone fixture: slope in x0 = %.3f (must be clearly negative)" % slope)
if slope > -1.0:
    sys.exit("  the fixture does not decrease in x0, so the constraint would be free")

# 3. the importance fixture's truth must be unambiguous: x0's contribution far above x1's,
#    and x1's far above the noise, or a 'correct' rank is luck.
c0, c1 = 5.0*Xi[:, 0].std(), 0.5*Xi[:, 1].std()
noise = rng.normal(scale=0.1, size=n).std()
print("  importance fixture: sd contributions x0=%.3f x1=%.3f noise=%.3f" % (c0, c1, noise))
if not (c0 > 3*c1 > 9*noise):
    sys.exit("  x0/x1/noise are not separated enough for the rank to be determined")
print("  all three fixtures can fail, so the assertions are not vacuous")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/gradient-boosting/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/gradient-boosting/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/gradient-boosting/  (no data staged -- every fixture is seeded)"
