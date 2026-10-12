#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched and nothing is synthesised from data: every
# reference here is either a published constant or a closed form.
#
# What is validated below is ONLY the references, with sunpy absent from the loop -- the
# series that predict the two controls' magnitudes, the four rotation laws' coefficients,
# and the axial tilt derived from the ICRF solar pole by hand. astropy is not installed
# locally, so the tilt is recomputed here with a literal J2000 obliquity while the run
# uses astropy's own; agreement between those two routes is part of the point.
# The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=sunpy does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the network must go down BEFORE the first import that could use it =="
awk '/^from astropy.utils import iers/      {iers=NR}
     /^data_conf.allow_internet = False/    {net=NR}
     /^import sunpy$/                       {if(!sp) sp=NR}
     END{
       if(!iers || !net || !sp){print "  missing the iers/allow_internet/sunpy lines"; exit 1}
       if(iers > sp || net > sp){
         printf "  the network is disabled at line %d/%d but sunpy is imported at %d\n", iers, net, sp
         exit 1}
       printf "  iers.auto_download off at line %d, allow_internet off at %d, sunpy imported at %d\n", iers, net, sp
     }' identities.py
grep -q 'v = v if isinstance(v, str) else str(v)' identities.py \
  || { echo "  rec() does not str() its value -- a Quantity would format as a tuple" >&2; exit 1; }
test "$(grep -c 'finite(' identities.py)" -ge 10 \
  || { echo "  too few finiteness guards; a NaN passes every one-sided bound here" >&2; exit 1; }
echo "  rec() is type-safe and the bounds are paired with finiteness guards"

echo "== every reference must be derivable without sunpy =="
python3 - <<'PY'
import sys
import numpy as np

R_IAU, R_OLD = 695700000.0, 696.0e6     # IAU 2015 Res B3; the pre-2015 value
AU = 149597870700.0                     # IAU 2012 Res B2 -- the definition
ARC = 3600.0 * 180.0 / np.pi

# 1. THE CONTROLS' MAGNITUDES ARE A SERIES, NOT AN OBSERVATION.
#    arcsin(x) - arctan(x) = x^3/2 + O(x^5)      arcsin(x) - x = x^3/6 + O(x^5)
#    so, relative to the angle itself, the two wrong forms sit x^2/2 and x^2/6 away.
print("  the angular radius of a sphere is arcsin(R/d); two plausible wrong forms:")
for lab, dist in (("perihelion 0.9833 au", 0.9833 * AU),
                  ("mean 1 au", AU),
                  ("aphelion 1.0167 au", 1.0167 * AU)):
    x = R_IAU / dist
    asin, atan, lin = np.arcsin(x) * ARC, np.arctan(x) * ARC, x * ARC
    r_tan, r_lin = abs(asin - atan) / asin, abs(asin - lin) / asin
    print("    %-22s arcsin %.6f  arctan off %.4e (x^2/2 = %.4e)  linear off %.4e "
          "(x^2/6 = %.4e)" % (lab, asin, r_tan, x * x / 2, r_lin, x * x / 6))
    for got, want, name in ((r_tan, x * x / 2, "arctan"), (r_lin, x * x / 6, "linear")):
        if abs(got / want - 1.0) > 1e-3:
            sys.exit("  the %s control's size is not predicted by the series" % name)
print("    both controls' sizes are predicted by the series to better than 0.1%")

# 2. THE ROTATION LAWS, from their published coefficients.
def murad(c, lat):
    s2 = np.sin(np.radians(lat)) ** 2
    return (c[0] + c[1] * s2 + c[2] * s2 ** 2) * 1e-6 * 86400.0 * 180.0 / np.pi

HOWARD, SNODGRASS = (2.894, -0.428, -0.370), (2.851, -0.343, -0.474)
LATS = (0.0, 15.0, 30.0, 45.0, 60.0, 75.0)
laws = {"howard (murad/s)": [murad(HOWARD, L) for L in LATS],
        "snodgrass (murad/s)": [murad(SNODGRASS, L) for L in LATS],
        "allen (deg/d)": [14.44 - 3.0 * np.sin(np.radians(L)) ** 2 for L in LATS],
        "rigid (deg/d)": [14.1844 for L in LATS]}
print("  four published rotation laws, deg/day sidereal, at %s deg:" % str(list(LATS)))
for name, seq in laws.items():
    print("    %-22s %s" % (name, " ".join("%8.5f" % v for v in seq)))
for name, seq in laws.items():
    flat = max(seq) - min(seq) == 0.0
    if name.startswith("rigid") != flat:
        sys.exit("  %s is %sflat in latitude, which is backwards" % (name, "" if flat else "not "))
    if not flat:
        if seq[0] - seq[-1] < 1.0:
            sys.exit("  %s falls only %.3f deg/day; too little to contrast with rigid"
                     % (name, seq[0] - seq[-1]))
        if not all(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
            sys.exit("  %s is not monotone poleward" % name)
print("    rigid is flat; the three differential laws fall monotonically poleward")

# 3. THE AXIAL TILT, from the ICRF solar pole by hand -- the run redoes this through
#    astropy's own obliquity, so these are two routes to one angle.
a0, d0 = np.radians(286.13), np.radians(63.87)        # Archinal et al. 2018
eps = np.radians(23.4392911)                          # J2000 mean obliquity
sp = np.array([np.cos(d0) * np.cos(a0), np.cos(d0) * np.sin(a0), np.sin(d0)])
ep = np.array([0.0, -np.sin(eps), np.cos(eps)])       # ecliptic north, equatorial frame
tilt = np.degrees(np.arccos(float(sp @ ep)))
print("  solar pole (286.13, 63.87) is %.6f deg from the ecliptic pole" % tilt)
print("    and %.6f deg from the EQUATORIAL pole -- the control" % (90.0 - np.degrees(d0)))
if not 7.0 < tilt < 7.5:
    sys.exit("  the derived tilt %.6f is not the ~7.25 deg solar inclination" % tilt)

# 4. Earth's mean orbital motion, which is the sidereal-synodic offset.
print("  Earth's mean motion 360/365.25636 = %.9f deg/day" % (360.0 / 365.25636))

# 5. THE LEGACY CONSTANT, explained before the run reports it.
print("  'average angular size' = 959.63 arcsec (Allen 4th ed.):")
print("    arcsin(695700 km / au) = %.6f   <- the table's own IAU 2015 radius, %.4f off"
      % (np.arcsin(R_IAU / AU) * ARC, 959.63 - np.arcsin(R_IAU / AU) * ARC))
print("    arcsin(696000 km / au) = %.6f   <- a pre-2015 radius, %.4f off"
      % (np.arcsin(R_OLD / AU) * ARC, 959.63 - np.arcsin(R_OLD / AU) * ARC))
if abs(959.63 - np.arcsin(R_OLD / AU) * ARC) > abs(959.63 - np.arcsin(R_IAU / AU) * ARC):
    sys.exit("  the legacy-radius explanation does not hold")
print("    so the constant is a correctly cited legacy value, not an error -- the table")
print("    mixes five sources and is not internally consistent")

# 6. The round-trip grid must lie ON the disc, or every round-trip is NaN. The binding
#    case is APHELION, where the disc is at its smallest.
ang_max = np.arcsin(R_IAU / (0.9833 * AU)) * ARC        # perihelion: the disc is largest
ang = np.arcsin(R_IAU / (1.0167 * AU)) * ARC            # aphelion: the disc is smallest
print("  the disc spans %.3f arcsec (aphelion) to %.3f (perihelion); the smaller bounds "
      "the grid" % (ang, ang_max))
for tx, ty in ((0, 0), (300, -200), (-500, 400), (600, 100), (-700, 300)):
    r = np.hypot(tx, ty)
    if r >= ang:
        sys.exit("  grid point (%d, %d) at %.1f arcsec is outside the %.1f arcsec disc"
                 % (tx, ty, r, ang))
print("  all five grid points are inside %.3f arcsec, the disc at aphelion" % ang)
print("  references are sound: two analytic controls, four published laws, one derived tilt")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/sunpy/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/sunpy/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/sunpy/  (no data staged -- every reference is published or analytic)"
