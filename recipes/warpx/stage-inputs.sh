#!/usr/bin/env bash
# Stage the checks. The DECK ships inside the image (WarpX's own Examples/Tests/langmuir), so
# there is nothing scientific to fetch. What this script does is pin the TOLERANCES: the two
# numbers this recipe asserts against are upstream's own, and they are verified here against
# WarpX's analysis scripts at the tag matching the package version. A tolerance copied from a
# changelog is a number someone typed; one checked against the source at a pinned tag is a
# reference.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=warpx does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TAG=26.10          # matches warpx 26.10 np2py314h16d739f_0 in the pic env
RAW="https://raw.githubusercontent.com/ECP-WarpX/WarpX/${TAG}/Examples/Tests/langmuir"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the asserted tolerances must be upstream's, at the matching tag =="
curl -fsSL -o analysis_2d.py    "$RAW/analysis_2d.py"
curl -fsSL -o analysis_utils.py "$RAW/analysis_utils.py"
python3 - <<'PY'
import re, sys
a = open("analysis_2d.py").read()
u = open("analysis_utils.py").read()
# The field tolerance for particle_shape != 4, which is what the deck uses (algo.particle_shape = 1)
m = re.search(r"else:\s*\n\s*tolerance_rel\s*=\s*([0-9.eE+-]+)", a)
if not m:
    sys.exit("  could not find tolerance_rel in upstream's analysis_2d.py")
field_tol = float(m.group(1))
print("  analysis_2d.py      tolerance_rel = %s" % field_tol)
if field_tol != 0.0503:
    sys.exit("  upstream's field tolerance is now %s, this recipe asserts 0.0503" % field_tol)
# The charge-conservation tolerance for esirkepov (the default branch, no current_correction/vay)
m = re.search(r"tolerance\s*=\s*1e-11", u)
if not m:
    sys.exit("  could not find the 1e-11 default charge tolerance in analysis_utils.py")
print("  analysis_utils.py   default charge tolerance = 1e-11")
# And that upstream really does gate the charge check on the deposition scheme, which is the
# reason this recipe runs esirkepov and only REPORTS direct.
for tok in ("current_deposition_esirkepov", "current_deposition_vay", "current_correction"):
    if tok not in u:
        sys.exit("  upstream no longer gates the charge check on %s -- re-derive" % tok)
print("  the charge check is gated on esirkepov / vay / current_correction, as the recipe assumes")
PY

echo "== the closed form, derived here before a box is paid for =="
python3 - <<'PY'
import sys
import numpy as np
from scipy.constants import c, e, epsilon_0, m_e
n0 = 2.0e24                       # per species, from the deck
n_total = 2.0 * n0                # electrons + positrons
wp = np.sqrt(n_total * e**2 / (m_e * epsilon_0))
kx = 2.0 * np.pi * 2.0 / 40e-6
amp = 0.01 * (m_e * c**2 * kx) / e
print("  n_total = %.3e m^-3  ->  wp = %.6e rad/s" % (n_total, wp))
print("  k = %.6e 1/m   field amplitude prefactor = %.6e V/m" % (kx, amp))
dx = 40e-6 / 128
dt = dx / (c * np.sqrt(2.0))      # 2D, cfl = 1
periods = 80 * dt * wp / (2 * np.pi)
print("  80 steps at cfl=1 = %.4f plasma periods" % periods)
if not (0.8 < periods < 2.0):
    sys.exit("  the run covers %.3f periods -- too few or too many to see the oscillation" % periods)
if abs(wp - 1.128292e14) / wp > 1e-6:
    sys.exit("  wp came out %.6e, expected 1.128292e+14" % wp)
print("  closed form checks out, and the run spans just over one oscillation")
PY

sha256sum identities.py analysis_2d.py analysis_utils.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/warpx/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/warpx/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/warpx/  (the deck ships in the image; only the checks are staged)"
