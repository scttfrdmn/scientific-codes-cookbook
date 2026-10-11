#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: the density field is analytic, so the reference is a
# closed form plus an independent numpy reduction of the same array.
#
# What is validated here is ONLY arithmetic that does not depend on the host -- the cell-centre
# sampling and the closed form. yt's behaviour is not checked locally and must not be: the
# instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=yt does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the closed form and the numpy reference must agree with each other =="
python3 - <<'PY'
import ast, io, sys
import numpy as np

RHO0, LBOX = 2.5, 1.0

# Take `analytic` FROM identities.py rather than restating it. An earlier version of this
# script carried its own copy of the field, they drifted, and the validation below was
# checking a function the run does not use. One definition, lifted by name.
tree = ast.parse(io.open("identities.py", encoding="utf-8").read())
src = next((ast.unparse(n) for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "analytic"), None)
if src is None:
    sys.exit("  identities.py has no analytic() to validate")
exec(compile(src, "<analytic from identities.py>", "exec"))

# 1. a uniform field's mass IS rho*L^3, with no quadrature error at all
cube = [[0.0, LBOX]]*3
n = 64
m = np.full((n,n,n), RHO0).sum() * (LBOX/n)**3
print("  uniform   numpy %.17e   closed form %.17e" % (m, RHO0*LBOX**3))
if m != RHO0*LBOX**3:
    sys.exit("  the uniform reference is not exact, so the in-run `==` would be wrong")

# 2. the field must be non-trivial on every axis, or the anisotropic box proves nothing.
# A field constant along an axis would survive a transpose of that axis undetected.
rho = analytic((32,48,64), [[0.,1.],[0.,2.],[0.,3.]])
for ax, name in ((0,"x"), (1,"y"), (2,"z")):
    # np.ptp(), not arr.ptp(): the method was removed in numpy 2.x
    spread = float(np.ptp(rho.mean(axis=tuple(a for a in (0,1,2) if a != ax))))
    print("  variation along %s: %.6f" % (name, spread))
    if spread < 1e-3:
        sys.exit("  the field is flat along %s, so a transposed axis could hide" % name)

# 3. the anisotropic box must be genuinely anisotropic in BOTH count and cell size
shape, extent = (32,48,64), [[0.,1.],[0.,2.],[0.,3.]]
d = [(hi-lo)/n for n,(lo,hi) in zip(shape, extent)]
print("  cell sizes  %s cm" % ", ".join("%.6f" % x for x in d))
if len(set(shape)) != 3 or len({round(x,12) for x in d}) != 3:
    sys.exit("  the 'anisotropic' box repeats a count or a cell size")

# 4. the sphere's analytic volume, and that the fixture radius leaves it inside the box
R = 0.3
V = 4.0/3.0*np.pi*R**3
print("  sphere V %.12f cm^3  fraction of box %.6f" % (V, V/LBOX**3))
if R >= LBOX/2:
    sys.exit("  the sphere is not strictly inside the box")
print("  references are sound: a closed form, an independent sum, and an analytic volume")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/yt/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/yt/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/yt/  (no data staged -- the density field is analytic)"
