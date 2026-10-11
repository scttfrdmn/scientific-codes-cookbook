#!/usr/bin/env bash
# Stage the checks. Nothing is fetched and no scene file is read: the geometry is two
# axis-aligned quads, so every expected pixel count is an integer derived from the viewport.
#
# Validated here is ONLY that arithmetic -- that the viewport sizes divide into halves and
# thirds exactly, so the run's `==` comparisons are against whole numbers. The GL stack is
# not exercised locally and must not be: the instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=mesalib does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the expected pixel counts must be exact integers =="
python3 - <<'PY'
import sys

# Half coverage: a quad over x in [-1,0] of a [-1,1] orthographic viewport covers exactly
# half the width, so N^2/2 pixels -- which must be a whole number.
for n in (64, 128, 256):
    if (n * n) % 2:
        sys.exit("  a %dx%d viewport has no exact half" % (n, n))
    print("  %3dx%-3d half coverage = %6d px" % (n, n, n * n // 2))

# Overlap: red spans x in [-1,1/3] and green x in [-1/3,1]; each is 2/3 of the width and
# they share the middle third. The counts are 2N^2/3 and N^2/3, so N^2 must divide by 3.
for n in (96, 192):
    if (n * n) % 3:
        sys.exit("  a %dx%d viewport does not divide into thirds" % (n, n))
    print("  %3dx%-3d overlap split  = %6d / %6d px (2/3, 1/3)"
          % (n, n, 2 * n * n // 3, n * n // 3))

# The two quads must genuinely overlap, or the depth test has nothing to decide.
red, green = (-1.0, 1.0 / 3.0), (-1.0 / 3.0, 1.0)
lo, hi = max(red[0], green[0]), min(red[1], green[1])
print("  overlap in world x: [%.4f, %.4f]  width %.4f of 2.0" % (lo, hi, hi - lo))
if hi <= lo:
    sys.exit("  the quads do not overlap, so the depth check is vacuous")
if abs((hi - lo) - 2.0 / 3.0) > 1e-12:
    sys.exit("  the overlap is not the middle third the counts assume")

# And each must cover MORE than the overlap, or 'who won the overlap' is unobservable
# (a quad that was entirely inside the overlap could not be distinguished).
for name, (a, b) in (("red", red), ("green", green)):
    if (b - a) <= (hi - lo):
        sys.exit("  %s is no wider than the overlap" % name)
print("  each quad is wider than the overlap, so the winner is observable")
print("  references are sound: integer pixel counts from fixed geometry")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/mesalib/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/mesalib/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/mesalib/  (no data staged -- the geometry is two quads)"
