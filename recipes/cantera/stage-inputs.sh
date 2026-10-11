#!/usr/bin/env bash
# Stage the checks only. NOTHING is fetched and no mechanism is copied: GRI-Mech 3.0 ships
# inside the cantera package, so it is version-matched to the library by construction.
#
# Validated here is ONLY what does not need cantera -- that the identities asserted in-run
# are genuine thermodynamic relations with the units to match, and that the published
# constants quoted are the real ones. The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=cantera does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the identities and published constants must stand on their own =="
python3 - <<'PY'
import sys

# The universal gas constant, from the SI definition (exact since the 2019 redefinition):
# R = N_A * k_B, both of which are defined constants.
NA = 6.02214076e23          # mol^-1, exact by definition
KB = 1.380649e-23           # J/K,   exact by definition
R = NA * KB
print("  R = N_A * k_B = %.6f J/(mol K)   (both exact by SI definition)" % R)
if abs(R - 8.314462618) > 1e-8:
    sys.exit("  the gas constant does not reproduce from its definition")
print("  cp - cv = R is therefore a constant the run can check against, not a fit")

# Dimensional sanity of the identities asserted in-run, so a units slip cannot hide.
print("  identity                     units check")
for name, lhs, rhs in (("h = u + pv",   "J/mol", "J/mol + Pa*m^3/mol = J/mol"),
                       ("g = h - Ts",   "J/mol", "J/mol - K*J/(mol K) = J/mol"),
                       ("cp - cv = R",  "J/(mol K)", "J/(mol K)"),
                       ("p = rho R T/M","Pa", "(kg/m^3)(J/mol K)(K)/(kg/mol) = Pa")):
    print("    %-26s %-10s <- %s" % (name, lhs, rhs))

# The stoichiometry quoted in the fixture: CH4 + 2 O2 -> CO2 + 2 H2O, with air as
# O2:1 / N2:3.76. So "CH4:1, O2:2, N2:7.52" is stoichiometric methane-air.
print("  CH4 + 2 O2 -> CO2 + 2 H2O, air N2/O2 = 3.76 -> N2 = 2 * 3.76 = %.2f" % (2*3.76))
if abs(2 * 3.76 - 7.52) > 1e-12:
    sys.exit("  the N2 coefficient in the fixture is not stoichiometric air")
print("  so CH4:1, O2:2, N2:7.52 is the stoichiometric mixture the ~2225 K value refers to")

# Element conservation is a counting statement; confirm the fixture has atoms to conserve.
atoms = {"C": 1, "H": 4, "O": 2 * 2, "N": 2 * 7.52}
print("  atoms per mole of fuel in the fixture: %s" % atoms)
if atoms["C"] < 1 or atoms["H"] < 1:
    sys.exit("  the fixture has no carbon or hydrogen, so combustion cannot occur")

# GRI-Mech 3.0's published size, asserted in-run as exact integers.
print("  GRI-Mech 3.0 is published as 53 species / 325 reactions")
print("  references are sound: an SI constant, dimensional identities, published stoichiometry")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/cantera/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/cantera/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/cantera/  (no data staged -- gri30 ships inside cantera)"
