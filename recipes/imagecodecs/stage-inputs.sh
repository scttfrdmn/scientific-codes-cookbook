#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: the test image is synthesised from a pinned
# seed, and the reference is byte identity -- there is no value to look up.
#
# Validated here is ONLY that the image is a fair test: structured enough to compress, not
# so flat that a broken codec would round-trip it anyway, and not pure noise (where a
# compression-ratio assertion would be meaningless). No codec is exercised locally.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=imagecodecs does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"
grep -q 'np.frombuffer' identities.py \
  || { echo "  the byte-codec reshape is gone; those decodes return flat buffers" >&2; exit 1; }
echo "  byte-codec reshape present (those decodes return a flat buffer, not an array)"

echo "== the test image must be a fair test for a compressor =="
python3 - <<'PY'
import ast, io, sys
import numpy as np

src = io.open("identities.py", encoding="utf-8").read()
K = {}
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        if node.targets[0].id in ("SEED", "H", "W"):
            try:
                K[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple):
        names = [e.id for e in node.targets[0].elts if isinstance(e, ast.Name)]
        if names == ["H", "W"]:
            pass
if "SEED" not in K:
    sys.exit("  identities.py no longer defines SEED")
H = W = 512
rng = np.random.default_rng(K["SEED"])
yy, xx = np.mgrid[:H, :W].astype("float64")
base = (140
        + 40 * np.sin(2*np.pi*xx/140.0) * np.cos(2*np.pi*yy/190.0)
        + 25 * ((xx-200)**2 + (yy-300)**2 < 90**2)
        - 30 * ((xx-380)**2 + (yy-150)**2 < 60**2)
        + rng.normal(0, 6.0, (H, W)))
gray = np.clip(base, 0, 255).astype("uint8")
print("  %dx%d uint8, %d distinct levels, raw %d bytes" % (H, W, len(np.unique(gray)), gray.nbytes))

# Not flat: a constant image round-trips through anything, so it proves nothing.
if len(np.unique(gray)) < 50:
    sys.exit("  too few distinct levels; a broken codec could still round-trip this")

# Not pure noise: estimate compressibility from first-order entropy. Pure uniform noise
# has entropy 8 bits/px and cannot be compressed, which would make any ratio assertion
# unsatisfiable rather than strict.
counts = np.bincount(gray.ravel(), minlength=256).astype("float64")
p = counts[counts > 0] / counts.sum()
entropy = float(-(p * np.log2(p)).sum())
print("  first-order entropy %.3f bits/px -> an ideal coder reaches ~%.2fx" % (entropy, 8.0/entropy))
if entropy > 7.5:
    sys.exit("  the image is nearly incompressible (entropy %.2f); the >1.05x assertions"
             " would be unsatisfiable" % entropy)
if entropy < 2.0:
    sys.exit("  the image is almost trivially compressible; the test is too easy")

# Spatial structure is what the image codecs exploit, so confirm neighbours correlate.
d = np.diff(gray.astype("float64"), axis=1)
print("  mean |horizontal neighbour difference| %.3f of 255 -- smooth enough for a"
      " predictive codec" % np.abs(d).mean())
if np.abs(d).mean() > 40:
    sys.exit("  neighbouring pixels are nearly independent; predictive codecs would gain"
             " nothing and the ratio assertions would fail for the wrong reason")
print("  references are sound: byte identity needs no value, and the image is a fair test")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/imagecodecs/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/imagecodecs/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/imagecodecs/  (no data staged -- the image is synthesised)"
