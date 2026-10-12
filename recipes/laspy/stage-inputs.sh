#!/usr/bin/env bash
# Stage the checks only. The CLOUD IS NOT RE-STAGED: this recipe reads the same
# autzen.laz that recipes/pointcloud staged and PDAL decoded, because the cross-check
# only means anything on identical bytes and a second copy is a second thing to keep true.
#
# Validated here is ONLY the arithmetic of the LAS quantisation law and the precision of
# PDAL's published mean -- no laspy. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=laspy does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the shared cloud must already be staged by pointcloud =="
if ! aws s3 ls --region "$REGION" "$BUCKET/inputs/pointcloud/autzen.laz" >/dev/null 2>&1; then
  echo "  missing: run 'make stage RECIPE=pointcloud' first -- this recipe deliberately" >&2
  echo "  does not stage its own copy of the cloud." >&2
  exit 1
fi
echo "  found $BUCKET/inputs/pointcloud/autzen.laz (shared with pointcloud)"

echo "== the quantisation law and PDAL's precision must stand without laspy =="
python3 - <<'PY'
import ast, io, sys
import numpy as np

src = io.open("identities.py", encoding="utf-8").read()
K = {}
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign):
        t = node.targets[0]
        if isinstance(t, ast.Tuple):
            names = [e.id for e in t.elts if isinstance(e, ast.Name)]
            try:
                vals = [ast.literal_eval(e) for e in node.value.elts]
            except Exception:
                continue
            if len(names) == len(vals):
                K.update(dict(zip(names, vals)))
        elif isinstance(t, ast.Name) and t.id in ("PDAL_COUNT", "PDAL_MEAN_Z",
                                                  "PDAL_MEAN_Z_PRECISION", "PINNED_SHA"):
            try:
                K[t.id] = ast.literal_eval(node.value)
            except Exception:
                pass
for k in ("SCALE", "OFFSET", "N", "PDAL_COUNT", "PDAL_MEAN_Z", "PDAL_MEAN_Z_PRECISION"):
    if k not in K:
        sys.exit("  identities.py no longer defines %s" % k)
SCALE, OFFSET = K["SCALE"], K["OFFSET"]
print("  quantisation: scale %g, offset %g -> grid step %g, half-step %g"
      % (SCALE, OFFSET, SCALE, SCALE / 2))

# An on-grid value is exactly representable as int32*scale + offset; an off-grid value
# cannot be, and must land within half a step. Demonstrated here in plain numpy.
rng = np.random.default_rng(20261012)
ints = rng.integers(-50000, 50000, K["N"])
ongrid = ints * SCALE + OFFSET
recovered = np.rint((ongrid - OFFSET) / SCALE)
print("  on-grid values recover their integers exactly: %s"
      % bool(np.array_equal(recovered, ints)))
if not np.array_equal(recovered, ints):
    sys.exit("  the on-grid construction is not exactly representable; the exactness"
             " assertion would be wrong")
off = ongrid + rng.uniform(-SCALE / 2, SCALE / 2, K["N"])
err = np.abs((np.rint((off - OFFSET) / SCALE) * SCALE + OFFSET) - off)
print("  off-grid quantisation error: max %.6e, bound %.6e" % (err.max(), SCALE / 2))
if err.max() > SCALE / 2 + 1e-15:
    sys.exit("  the off-grid error exceeds half a step, so the bound is wrong")
if err.max() < SCALE / 10:
    sys.exit("  the off-grid perturbation is too small to force visible quantisation")

# int32 must actually hold the range, or the format cannot store these points.
span = (ints.max() - ints.min())
print("  integer span %d, int32 capacity %d" % (span, 2**31 - 1))
if span >= 2**31 - 1:
    sys.exit("  the coordinate range overflows int32")

# PDAL published its mean to four decimals, so the comparison precision is half the last
# digit. Asserting tighter would claim agreement PDAL never stated.
print("  PDAL mean Z published as %.4f -> comparison precision %.0e"
      % (K["PDAL_MEAN_Z"], K["PDAL_MEAN_Z_PRECISION"]))
if abs(K["PDAL_MEAN_Z_PRECISION"] - 5e-5) > 1e-12:
    sys.exit("  the precision does not match four published decimals")
print("  PDAL point count %d (autzen's documented, citable count)" % K["PDAL_COUNT"])
print("  references are sound: an exact format law and a reference at its own precision")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/laspy/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/laspy/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/laspy/  (the cloud is NOT re-staged -- shared with pointcloud)"
