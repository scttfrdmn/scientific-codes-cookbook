#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: the spectrum is synthesised and both references
# are analytic -- an equivalent width in closed form and an integral via erf.
#
# Validated here is ONLY the mathematics, independently of specutils: that the EW formula
# and the erf integral are right, and that the coarse grids really are coarse enough for
# the control to bite. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=specutils does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"
grep -q 'from specutils import Spectrum,' identities.py \
  || { echo "  not using the current Spectrum class (Spectrum1D is deprecated)" >&2; exit 1; }
echo "  uses specutils' current Spectrum class (Spectrum1D is deprecated in 2.x)"

echo "== both references must be derivable without specutils =="
python3 - <<'PY'
import math, sys
import numpy as np
from scipy.special import erf

LAM0, SIGMA, DEPTH = 6562.8, 3.0, 0.4
LAM_LO, LAM_HI, NFINE = 6400.0, 6700.0, 4001
A, B = 6420.0, 6680.0

# 1. The equivalent width in closed form, checked against a quadrature of the profile.
EW = DEPTH * SIGMA * math.sqrt(2.0 * math.pi)
lam = np.linspace(LAM_LO, LAM_HI, NFINE)
prof = DEPTH * np.exp(-0.5 * ((lam - LAM0) / SIGMA) ** 2)
quad = float(np.trapezoid(prof, lam))
print("  EW = d*sigma*sqrt(2pi) = %.12f A" % EW)
print("  quadrature of the profile on the fine grid = %.12f A  (rel %.3e)"
      % (quad, abs(quad - EW) / EW))
if abs(quad - EW) / EW > 1e-12:
    sys.exit("  the closed form does not match a quadrature of the same profile")

# 2. The erf integral, checked where a trapezoid cannot differ from it: the FULL range,
# which has no partial end intervals. This is the check that caught a bad sub-range
# reference producing a phantom 1.9e-04 error.
def analytic(a, b):
    g = DEPTH * SIGMA * math.sqrt(math.pi / 2.0) * (
        erf((b - LAM0) / (SIGMA * math.sqrt(2.0))) - erf((a - LAM0) / (SIGMA * math.sqrt(2.0))))
    return (b - a) - g

flux = 1.0 - prof
full = float(np.trapezoid(flux, lam))
print("  full-range: analytic %.12f  trapezoid %.12f  rel %.3e"
      % (analytic(LAM_LO, LAM_HI), full, abs(analytic(LAM_LO, LAM_HI) - full) / full))
if abs(analytic(LAM_LO, LAM_HI) - full) / full > 1e-12:
    sys.exit("  the erf integral disagrees with a trapezoid over the full range")

sub = float(np.trapezoid(flux[(lam >= A) & (lam <= B)], lam[(lam >= A) & (lam <= B)]))
print("  sub-range: analytic %.12f  trapezoid %.12f  DIFFER by %.4f"
      % (analytic(A, B), sub, abs(analytic(A, B) - sub)))
print("    ^ partial end intervals, not a conservation error -- the trap this recipe records")

# 3. The coarse grids must actually be coarse relative to sigma, or the control cannot bite.
print("  grid spacings in the sweep, against sigma = %.1f A:" % SIGMA)
for npts in (1301, 131, 53, 27):
    dl = (B - A) / (npts - 1)
    print("    %4d points -> %6.3f A  (%5.2f samples per sigma)" % (npts, dl, SIGMA / dl))
coarsest = (B - A) / (27 - 1)
if SIGMA / coarsest > 1.0:
    sys.exit("  even the coarsest grid still samples sigma more than once; an interpolator"
             " would stay near-exact and the control would not discriminate")
print("  the coarsest grid undersamples the line, so an interpolator must lose flux")
print("  references are sound: a verified closed form, a verified integral, biting grids")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/specutils/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/specutils/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/specutils/  (no data staged -- the spectrum is synthesised)"
