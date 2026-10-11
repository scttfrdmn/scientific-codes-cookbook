#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: the references are published astronomical constants
# (JD 2451545.0, MJD 51544), the CF calendar definitions, and two implementations that ship
# with Python itself.
#
# Validated here is ONLY what does not depend on the host: that the published constants are
# reachable from stdlib arithmetic alone, so the in-run assertion is against an external
# fact rather than against cftime's own opinion. cftime is not exercised locally and must
# not be -- the instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=cftime does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the published constants must be independently reachable =="
python3 - <<'PY'
import datetime as dt
import sys

# MJD is defined as JD - 2400000.5, with MJD 0 at 1858-11-17 00:00. Both the Julian Day
# and Modified Julian Date targets asserted in the run are checked here against stdlib
# arithmetic alone, so neither number is taken on cftime's word.
mjd = (dt.date(2000, 1, 1) - dt.date(1858, 11, 17)).days
print("  MJD of 2000-01-01 from stdlib: %d   (published 51544)" % mjd)
if mjd != 51544:
    sys.exit("  stdlib does not reproduce the published MJD; the reference is wrong")

# J2000.0 = JD 2451545.0 = 2000-01-01 12:00. JD 0 is -4713-11-24 12:00 proleptic
# Gregorian, which stdlib cannot represent (year < 1), so the span is checked from MJD 0
# and the published offset JD = MJD + 2400000.5 is applied.
jd = mjd + 2400000.5 + 0.5          # +0.5 day: J2000.0 is NOON, MJD 0 is midnight
print("  JD of 2000-01-01 12:00 via MJD: %.1f   (published 2451545.0)" % jd)
if jd != 2451545.0:
    sys.exit("  the JD/MJD offset does not close; the reference is wrong")

# The reform gap and the leap rules asserted in the run are definitions, so what is
# checked is that they are internally consistent with the Gregorian rule stdlib uses.
greg_leap = lambda y: (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
for y, want in ((1900, False), (2000, True), (2100, False)):
    if greg_leap(y) != want:
        sys.exit("  the Gregorian leap rule is misstated for %d" % y)
    try:
        dt.date(y, 2, 29); have = True
    except ValueError:
        have = False
    if have != want:
        sys.exit("  stdlib disagrees with the Gregorian leap rule at %d" % y)
print("  Gregorian leap rule agrees with stdlib at 1900, 2000, 2100")

# Julian's rule is every 4th year, with no century exception -- the divergence the run
# asserts. Stated here so the expectation is not circular on cftime.
for y in (1900, 2000, 2100):
    if y % 4 != 0:
        sys.exit("  %d is not a Julian leap year, so the divergence test is wrong" % y)
print("  Julian rule (every 4th year) makes 1900, 2000, 2100 all leap -- the divergence")

span = (dt.date(2400, 1, 1) - dt.date(1600, 1, 1)).days
print("  cross-check span: %d days" % span)
if span != 292194:
    sys.exit("  the span is %d days, not the 292194 the page quotes" % span)
print("  references are sound: published constants, checked without cftime")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/cftime/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/cftime/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/cftime/  (no data staged -- the references are published constants)"
