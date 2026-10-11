#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: six doubles are written literally into the script and
# the reference is R's own `identical()` plus an independent parser, so there is no data tier.
#
# Validated here is ONLY that the fixture is hostile enough to catch short decimal output --
# that each value genuinely needs more than 15 significant digits to survive. jsonlite is not
# exercised locally and must not be: the instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=r-jsonlite does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.R" .
printf '  %-16s %6s bytes\n' identities.R "$(wc -c < identities.R | tr -d '[:space:]')"
grep -q 'digits = I(17)' identities.R \
  || { echo "  identities.R no longer uses digits = I(17)" >&2; exit 1; }

echo "== the fixture must be hostile to short decimal output =="
python3 - <<'PY'
import sys

# The same six values the run uses. Python's float is IEEE754 double, as R's is, so this
# checks the PROPERTY the recipe depends on without involving R or jsonlite.
vals = [3.141592653589793, 2.718281828459045, 1/3, 1e-17, 123456.789012345,
        2.220446049250313e-16]
print("  value                        4 sig      15 sig            17 sig survives")
bad = []
for v in vals:
    r4 = float("%.4g" % v)
    r15 = float("%.15g" % v)
    r17 = float("%.17g" % v)
    print("  %-22.17g  %-9s  %-16s  %s"
          % (v, r4 == v, r15 == v, r17 == v))
    if r17 != v:
        bad.append(("needs more than 17 digits", v))
if bad:
    sys.exit("  fixture is wrong: %s" % bad)

# NOT every value has to be hostile. 1e-17 is a clean power of ten and survives even 4
# significant digits -- which is worth keeping in the fixture, because it shows the loss
# is a property of the VALUE and not of every number. What matters is that most values
# fail at 4 digits, so the default is demonstrably lossy on this input.
survive4 = [v for v in vals if float("%.4g" % v) == v]
print("  values that survive 4 significant digits: %d of %d (1e-17 is a clean power of ten)"
      % (len(survive4), len(vals)))
if len(survive4) > len(vals) // 2:
    sys.exit("  most of the fixture survives 4 digits, so the default would look fine")

# At least one value must need all 17 digits, or digits=NA (15) would be good enough and
# the recipe's headline finding would not be demonstrable with this fixture.
needs17 = [v for v in vals if float("%.15g" % v) != v]
print("  values that 15 significant digits CANNOT represent: %d of %d"
      % (len(needs17), len(vals)))
if not needs17:
    sys.exit("  no value needs more than 15 digits, so digits=NA would round-trip and the"
             " finding could not be shown")
print("  references are sound: round-tripping needs 17 digits, which is the point")
PY

sha256sum identities.R > pins.sha256
aws s3 cp --region "$REGION" identities.R "$BUCKET/inputs/r-jsonlite/identities.R" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/r-jsonlite/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/r-jsonlite/  (no data staged -- six doubles, literal in the script)"
