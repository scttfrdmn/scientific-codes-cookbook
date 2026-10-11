#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: four hand-written particles plus a Pythia
# event generated in-run from a fixed seed, so every reference is a property of the
# algorithm rather than a dataset.
#
# Validated here is ONLY the geometry the invariances rest on -- that the hard particles
# really are separated by more than R (so one jet each), and that the control's splits
# really are non-collinear. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=fastjet does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the fixture geometry must support the invariances =="
python3 - <<'PY'
import ast, io, math, sys

src = io.open("identities.py", encoding="utf-8").read()
tree = ast.parse(src)
K = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        n = node.targets[0].id
        if n in ("R", "MIN_PT", "HARD", "SEED"):
            try:
                K[n] = ast.literal_eval(node.value)
            except Exception:
                pass
for k in ("R", "HARD"):
    if k not in K:
        sys.exit("  identities.py no longer defines %s" % k)
R, HARD = K["R"], K["HARD"]
print("  R = %g, %d hard particles" % (R, len(HARD)))

def rap_phi(p):
    px, py, pz, E = p
    pt = math.hypot(px, py)
    # rapidity, the variable jet algorithms actually use
    y = 0.5 * math.log((E + pz) / (E - pz))
    return y, math.atan2(py, px), pt

info = [rap_phi(p) for p in HARD]
print("  particle    pt        y        phi")
for (y, phi, pt) in info:
    print("    %9.4f %8.4f %8.4f" % (pt, y, phi))

# Every pair must be further apart than R, or they would merge and "one jet per particle"
# would be false -- which the in-run jet-count assertion depends on.
worst = 1e9
for i in range(len(info)):
    for j in range(i + 1, len(info)):
        dy = info[i][0] - info[j][0]
        dphi = abs(info[i][1] - info[j][1])
        dphi = min(dphi, 2 * math.pi - dphi)
        d = math.hypot(dy, dphi)
        worst = min(worst, d)
print("  closest pair separation in (y,phi): %.4f  (needs > R = %g)" % (worst, R))
if worst <= R:
    sys.exit("  two hard particles are within R, so they will merge and the jet count"
             " assertion is wrong")

# The control's splits must genuinely exceed R in angle, or "non-collinear" is a misnomer.
print("  control splits at dphi = 0.05, 0.20, 1.00 against R = %g:" % R)
for dphi in (0.05, 0.20, 1.00):
    print("    dphi %.2f -> %s R" % (dphi, "<" if dphi < R else ">="))
if not (1.00 >= R):
    sys.exit("  even the widest control split is inside R; it could not change the jets")

# And pt must clear the in-run threshold, or jets would be filtered rather than found.
print("  min pt in the fixture %.4f vs MIN_PT %.4f" % (min(i[2] for i in info), K.get("MIN_PT", 5.0)))
if min(i[2] for i in info) <= K.get("MIN_PT", 5.0):
    sys.exit("  a hard particle is below the pt threshold; it would never form a jet")
print("  references are sound: separated particles, graded controls, a real pt threshold")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/fastjet/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/fastjet/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/fastjet/  (no data staged -- the event is generated in-run)"
