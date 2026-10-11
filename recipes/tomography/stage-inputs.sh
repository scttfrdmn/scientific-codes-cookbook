#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: the phantom is a uniform disc, so its mass is
# a closed form and the Radon identities need no reference data at all.
#
# Validated here is ONLY what does not need astra -- the disc's analytic mass, that the
# metric separates a reconstruction from a wrong image, and the float32 floor the in-run
# bounds are derived from. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=tomography does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"
grep -q 'if __name__ == "__main__"' identities.py \
  || { echo "  identities.py lost its __main__ guard; tomopy will crash under python 3.14" >&2; exit 1; }
echo "  __main__ guard present (required: python 3.14 defaults to forkserver)"

echo "== the phantom's mass and the metric must be sound without astra =="
python3 - <<'PY'
import ast, io, math, sys
import numpy as np

src = io.open("identities.py", encoding="utf-8").read()
K = {}
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        if node.targets[0].id in ("N", "R", "N_ANG", "SEED"):
            try:
                K[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
for k in ("N", "R", "N_ANG"):
    if k not in K:
        sys.exit("  identities.py no longer defines %s" % k)
N, R = K["N"], K["R"]
print("  %dx%d grid, disc radius %g, %d angles" % (N, N, R, K["N_ANG"]))
if R >= N / 2:
    sys.exit("  the disc does not fit inside the grid")

# The rasterised disc versus pi*R^2 -- the mass reference the run asserts against.
yy, xx = np.mgrid[:N, :N].astype("float64")
c = (N - 1) / 2.0
disc = ((yy - c) ** 2 + (xx - c) ** 2 <= R * R).astype("float64")
frac = disc.sum() / (math.pi * R * R)
print("  rasterised mass %.1f px, analytic %.3f, ratio %.6f" % (disc.sum(), math.pi*R*R, frac))
if abs(frac - 1.0) > 0.01:
    sys.exit("  rasterisation is %.4f of the analytic area; too coarse to be a reference" % frac)

# The metric must separate a reconstruction from a wrong answer, or asserting a threshold
# on it proves nothing. Checked here on wrong images alone -- no astra needed.
def norm_err(img):
    m = img[disc > 0].mean()
    return float(np.linalg.norm(img / m - disc) / np.linalg.norm(disc))
rng = np.random.default_rng(K.get("SEED", 0))
print("  metric on deliberately wrong images:")
worst = 9e9
for name, img in (("gaussian noise", rng.normal(size=disc.shape) + 1.0),
                  ("disc shifted 40px", np.roll(disc, 40, axis=1) + 1e-9),
                  ("uniform field", np.ones_like(disc))):
    e = norm_err(img)
    print("    %-20s %.6f" % (name, e))
    worst = min(worst, e)
print("  closest wrong image scores %.4f, so a recon under ~0.1 is clearly separated" % worst)
if worst < 0.5:
    sys.exit("  a wrong image scores below 0.5; the metric does not discriminate enough"
             " for the in-run threshold to mean anything")

# float32 is where the in-run bounds come from, not from the observed numbers.
print("  float32 eps %.3e -> a 256-sample ray sum floors linearity near 1e-6"
      % np.finfo("float32").eps)
print("  references are sound: a closed-form mass, a discriminating metric, a dtype floor")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/tomography/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/tomography/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/tomography/  (no data staged -- the phantom is analytic)"
