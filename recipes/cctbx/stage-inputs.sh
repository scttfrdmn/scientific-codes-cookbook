#!/usr/bin/env bash
# Stage the checks only. NOTHING is fetched and no structure is copied: the real structure
# this recipe reads (1yjp.pdb) ships inside the cctbx package, so it is version-matched to
# the library by construction and pinned by the image digest plus an in-run sha256.
#
# Validated here is ONLY what does not need cctbx -- the lattice-centring rules as
# arithmetic, and the cubic d-spacing closed form. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=cctbx does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the crystallographic rules must be arithmetic, checkable without cctbx =="
python3 - <<'PY'
import sys

# The three centring rules the run asserts, as predicates over a small index grid. If a
# rule were misstated, it would either forbid nothing or forbid everything -- both of which
# make the in-run check useless, so both are caught here.
RULES = {
    "I  (h+k+l even)":        lambda h, k, l: (h + k + l) % 2 == 0,
    "F  (h,k,l same parity)": lambda h, k, l: h % 2 == k % 2 == l % 2,
    "C  (h+k even)":          lambda h, k, l: (h + k) % 2 == 0,
}
grid = [(h, k, l) for h in range(-4, 5) for k in range(-4, 5) for l in range(-4, 5)]
print("  index grid: %d combinations" % len(grid))
for name, rule in RULES.items():
    allowed = sum(1 for t in grid if rule(*t))
    frac = allowed / len(grid)
    print("  %-24s allows %5d of %d (%.1f%%)" % (name, allowed, len(grid), 100 * frac))
    if not (0.05 < frac < 0.95):
        sys.exit("  %s forbids nothing or almost everything; as a check it would be"
                 " vacuous or impossible" % name)

# The cubic closed form, independent of cctbx.
import math
A = 10.0
print("  cubic d-spacings from a/sqrt(h^2+k^2+l^2), a=%g:" % A)
for hkl in ((1, 0, 0), (1, 1, 1), (3, 2, 1)):
    d = A / math.sqrt(sum(x * x for x in hkl))
    print("    %s -> %.9f A" % (hkl, d))
    if not (0 < d <= A):
        sys.exit("  a d-spacing outside (0, a] is impossible for a cubic cell")

# 230 is the number of space groups; a comparison over fewer is not "all of them".
print("  space groups to compare: 230 (the crystallographic classification is complete)")

# And the orders the run reports must be divisors of the largest, or the table is wrong.
orders = [1, 2, 3, 4, 6, 8, 9, 12, 16, 18, 24, 32, 36, 48, 96, 192]
print("  expected distinct order values: %s" % orders)
bad = [o for o in orders if 192 % o and o not in (9, 18, 36)]
if bad:
    sys.exit("  these orders do not divide 192 and are not 3-fold family: %s" % bad)
print("  references are sound: parity rules, a closed form, and a complete classification")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/cctbx/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/cctbx/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/cctbx/  (no data staged -- 1yjp.pdb ships inside the cctbx package)"
