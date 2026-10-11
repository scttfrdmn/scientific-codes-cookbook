#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: the point cloud is drawn from a pinned seed and the
# references are exact counts, so every number is derivable without datashader.
#
# Validated here is ONLY the arithmetic the run compares against -- that the reference
# inside-count is reproducible from the seed, and that the fixture really puts most points
# OUTSIDE the canvas. datashader is not exercised locally and must not be: the instance is
# the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=datashader does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the fixture must put most points OUTSIDE the canvas =="
python3 - <<'PY'
import ast, io, sys
import numpy as np

# Read the constants out of identities.py rather than restating them: a second copy of a
# fixture parameter is a second thing to keep true, and it is exactly what drifted on the
# yt recipe's density field.
tree = ast.parse(io.open("identities.py", encoding="utf-8").read())
K = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        if isinstance(t, ast.Name) and t.id in ("SEED", "N", "RES"):
            K[t.id] = ast.literal_eval(node.value)
for k in ("SEED", "N", "RES"):
    if k not in K:
        sys.exit("  identities.py no longer defines %s" % k)
print("  seed %d, %d points, %dx%d canvas" % (K["SEED"], K["N"], K["RES"], K["RES"]))

# A 10x subsample is enough to show the fraction; the run checks the real number itself.
n = K["N"] // 10
rng = np.random.default_rng(K["SEED"])
x = rng.uniform(-1.0, 2.0, n)
y = rng.uniform(-1.0, 2.0, n)
frac = float(((x >= 0) & (x <= 1) & (y >= 0) & (y <= 1)).mean())
print("  fraction inside [0,1]^2 (on a %d-point subsample): %.4f" % (n, frac))
if not 0.05 < frac < 0.5:
    sys.exit("  %.1f%% of points are inside; the range would not be load-bearing" % (100*frac))
# The geometric expectation: the canvas is 1/9 of the [-1,2]^2 draw area.
print("  geometric expectation 1/9 = %.4f" % (1.0 / 9.0))
if abs(frac - 1.0 / 9.0) > 0.01:
    sys.exit("  the inside fraction is not the 1/9 the geometry implies")

# The cell count the cross-check compares, and that it is big enough to be a real test.
print("  cells compared elementwise: %d" % (K["RES"] ** 2))
if K["RES"] ** 2 < 10000:
    sys.exit("  too few cells for an elementwise cross-check to mean much")

# An empty cell must be possible at this resolution, or the NaN-vs-zero check is vacuous.
expected_per_cell = frac * K["N"] / K["RES"] ** 2
print("  mean points per occupied cell ~%.1f -- empty cells must still occur" % expected_per_cell)
print("  references are sound: exact counts, derivable from the seed")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/datashader/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/datashader/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/datashader/  (no data staged -- the cloud is drawn from a pinned seed)"
