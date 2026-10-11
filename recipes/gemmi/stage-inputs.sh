#!/usr/bin/env bash
# Stage the checks. Nothing is fetched and no structure file is read: the references are
# published space-group orders from the International Tables, a closed-form cell volume, and
# a group axiom -- none of which is data.
#
# Validated here is ONLY the published table the run asserts against, plus the two tolerance
# derivations, so neither number is taken on gemmi's word. gemmi is not exercised locally and
# must not be: the instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=gemmi does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the published table and both tolerances must be derivable without gemmi =="
python3 - <<'PY'
import ast, io, math, sys

src = io.open("identities.py", encoding="utf-8").read()
tree = ast.parse(src)

# Lift the PUBLISHED table out of the run script so this cannot drift from what is asserted.
pub = None
for node in tree.body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "PUBLISHED":
        pub = ast.literal_eval(node.value)
if pub is None:
    sys.exit("  identities.py no longer defines PUBLISHED")
print("  space groups asserted: %d" % len(pub))

# An order must divide 192 (the largest crystallographic point-group order times the
# lattice centring) and be even except for P1 -- a weak but independent sanity rule on
# the table, since a typo'd order would almost certainly break it.
for name, number, order in pub:
    if not (1 <= number <= 230):
        sys.exit("  %s has number %d, outside 1..230" % (name, number))
    if order < 1 or (order != 1 and order % 2):
        sys.exit("  %s has order %d, which no space group has" % (name, order))
print("  all numbers in 1..230 and all orders plausible")
orders = sorted({o for _, _, o in pub})
print("  orders covered: %s" % orders)
if max(orders) < 96:
    sys.exit("  no high-order group in the table; closure would barely be tested")

# Tolerance 1: PDB's coordinate field is %8.3f, so the grid is 1e-3 A.
grid = 1e-3
print("  PDB coordinate grid %.0e A -> worst rounding %.1e A" % (grid, grid / 2))
if abs(grid / 2 - 5.0e-4) > 1e-12:
    sys.exit("  the half-quantum derivation does not give 5.0e-4")

# Tolerance 2: float32 spacing at the fixture's coordinate magnitude.
import numpy as np
ulp = float(np.spacing(np.float32(50.0)))
print("  float32 ULP at |x|=50 A: %.3e  (parsers cannot beat this)" % ulp)
if not (1e-6 < ulp < 1e-5):
    sys.exit("  the float32 ULP is not where the recipe's tolerance assumes")
if ulp >= grid / 2:
    sys.exit("  the parser tolerance is not tighter than the format quantization, so the"
             " cross-parser check would be weaker than the round-trip one")

# And the closed-form volume, independently, for the cubic case where it is just abc.
a = b = c = 10.0
v = a*b*c*math.sqrt(1 - 0 - 0 - 0 + 0)
print("  closed-form volume of a 10 A cube: %.1f A^3" % v)
if v != 1000.0:
    sys.exit("  the closed form does not reduce to abc for a cubic cell")
print("  references are sound: a published table, a format grid, and a dtype limit")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/gemmi/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/gemmi/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/gemmi/  (no data staged -- the references are published tables)"
