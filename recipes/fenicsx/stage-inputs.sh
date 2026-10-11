#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: this is the method of manufactured solutions, so the
# reference is arithmetic. What the script verifies locally is that the arithmetic the run will
# be held to is right -- a wrong manufactured source term would be chased on a paid box.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=fenicsx does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the manufactured solutions, derived here with sympy-free arithmetic =="
python3 - <<'PY'
import sys
import numpy as np

# Case 1: u = 1 + x^2 + 2y^2  =>  -laplacian(u) = -(2 + 4) = -6, a constant.
# The recipe derives f symbolically with ufl, so this is the independent check on that.
print("  u = 1 + x^2 + 2y^2   ->  -lap(u) = -6 (constant)")
h = 1e-5
f = lambda x, y: 1.0 + x**2 + 2.0*y**2
for (x, y) in ((0.3, 0.7), (0.5, 0.5), (0.9, 0.1)):
    lap = ((f(x+h, y) - 2*f(x, y) + f(x-h, y)) +
           (f(x, y+h) - 2*f(x, y) + f(x, y-h))) / h**2
    if abs(-lap - (-6.0)) > 1e-3:
        sys.exit("  -lap(u) at (%.1f,%.1f) is %.6f, expected -6" % (x, y, -lap))
print("  confirmed at three points by finite difference")

# Case 2: u = sin(pi x) sin(pi y)  =>  -lap(u) = 2 pi^2 u
print("  u = sin(pi x) sin(pi y)  ->  -lap(u) = 2*pi^2*u = %.6f * u" % (2*np.pi**2))
g = lambda x, y: np.sin(np.pi*x)*np.sin(np.pi*y)
for (x, y) in ((0.3, 0.7), (0.25, 0.4)):
    lap = ((g(x+h, y) - 2*g(x, y) + g(x-h, y)) +
           (g(x, y+h) - 2*g(x, y) + g(x, y-h))) / h**2
    want = 2*np.pi**2*g(x, y)
    if abs(-lap - want)/abs(want) > 1e-4:
        sys.exit("  -lap(u) at (%.2f,%.2f) is %.6f, expected %.6f" % (x, y, -lap, want))
print("  confirmed at two points by finite difference")

# And that it vanishes on the boundary, so the second case's Dirichlet BC is homogeneous.
edge = max(abs(g(t, 0.0)) + abs(g(t, 1.0)) + abs(g(0.0, t)) + abs(g(1.0, t))
           for t in np.linspace(0, 1, 101))
print("  max |u| on the boundary = %.3e (homogeneous BC)" % edge)
if edge > 1e-12:
    sys.exit("  the sine solution does not vanish on the boundary")

# A quadratic is in P2 but not P1 -- the premise of the containment check and its control.
print("  a quadratic lies in P2/P3 and not in P1, which is why P1 is the control")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/fenicsx/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/fenicsx/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/fenicsx/  (no data staged -- the reference is arithmetic)"
