#!/usr/bin/env bash
# Stage the checks. Nothing scientific is fetched: the data is generated from a seeded RNG, and
# every reference is either an exact identity (a boost cannot change an invariant mass, a
# histogram partitions its fills), an independent implementation already in the image
# (scipy.special, uproot), or an uncertainty the tool reports about itself (the fit errors).
# What this script does verify locally is the arithmetic the run will be held to.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=root does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .

echo "== the checks must parse =="
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the closed forms and the fixture, derived here before a box is paid for =="
python3 - <<'PY'
import sys
import numpy as np

SEED, N, MU, SIGMA = 20261011, 20000, 0.35, 1.25
rng = np.random.default_rng(SEED)
x = rng.normal(MU, SIGMA, N).astype(np.float64)
print("  seeded sample: n=%d  mean=%.6f  std=%.6f" % (N, x.mean(), x.std(ddof=0)))
# The fit is asserted against ROOT's own errors, but the sample must actually be able to
# recover the truth: the standard error on the mean is sigma/sqrt(n).
sem = SIGMA / np.sqrt(N)
if abs(x.mean() - MU) > 5 * sem:
    sys.exit("  the seeded sample's mean is %.2f SEM from MU -- pick another seed"
             % (abs(x.mean() - MU) / sem))
print("  sample mean is %.2f SEM from MU=%.2f (SEM=%.5f), so the fit can recover it"
      % (abs(x.mean() - MU) / sem, MU, sem))

# Every fill must land inside the histogram range, or the partition check tests nothing:
# under/overflow would absorb entries and the assertion would still pass trivially.
LO, HI = -6.0, 6.0
outside = int(((x < LO) | (x >= HI)).sum())
print("  fills outside [%.1f, %.1f): %d" % (LO, HI, outside))
if outside:
    sys.exit("  %d fills would land in under/overflow -- widen the range" % outside)

# The invariant mass of the probe 4-vector must be real and well away from zero, or
# "unchanged under boost" is a statement about noise.
px, py, pz, E = 30.0, 40.0, 120.0, 200.0
m2 = E * E - (px * px + py * py + pz * pz)
if m2 <= 0:
    sys.exit("  the test 4-vector is not timelike (m^2 = %.3f)" % m2)
print("  test 4-vector: m = %.17f  (m^2 = %.1f, timelike)" % (np.sqrt(m2), m2))
print("  all closed forms check out")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/root/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/root/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/root/  (no data staged -- the sample is seeded, the references are identities)"
