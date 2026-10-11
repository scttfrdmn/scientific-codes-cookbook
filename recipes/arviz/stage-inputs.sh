#!/usr/bin/env bash
# Stage the checks. Nothing is fetched: the draws are synthesised with a known autocorrelation,
# so the references are a closed form and an exact invariance. What this script verifies locally
# is that the AR(1) construction really has the correlation the closed form assumes -- if it did
# not, the ESS check would be measured against the wrong number.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=arviz does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the AR(1) construction must have the correlation the closed form assumes =="
python3 - <<'PY'
import sys
import numpy as np

SEED, NCHAIN, NDRAW = 20261011, 4, 8000
rng = np.random.default_rng(SEED)

def ar1(rho, n=NDRAW, c=NCHAIN):
    eps = rng.normal(scale=np.sqrt(1 - rho**2), size=(c, n))
    x = np.empty((c, n)); x[:, 0] = rng.normal(size=c)
    for t in range(1, n):
        x[:, t] = rho*x[:, t-1] + eps[:, t]
    return x

print("  rho    measured lag-1 corr   marginal sd   closed-form ESS/N")
for rho in (0.0, 0.5, 0.8, 0.9):
    x = ar1(rho)
    # lag-1 correlation, pooled across chains
    a = np.concatenate([c[:-1] for c in x]); b = np.concatenate([c[1:] for c in x])
    r = float(np.corrcoef(a, b)[0, 1])
    sd = float(x.std(ddof=1))
    print("  %.1f    %+.4f               %.4f        %.4f" % (rho, r, sd, (1-rho)/(1+rho)))
    if abs(r - rho) > 0.02:
        sys.exit("  the construction gives lag-1 corr %.4f, not %.2f -- the closed form would "
                 "be compared against the wrong process" % (r, rho))
    if abs(sd - 1.0) > 0.05:
        sys.exit("  marginal sd is %.4f, not ~1 -- the scaling is wrong" % sd)
print("  the process matches its parameter, so (1-rho)/(1+rho) is the right reference")

# And the invariance fixture must have R-hat distinguishable from 1, or 'invariant' is trivial:
# an estimator returning exactly 1.0 always would also be invariant.
print("  (the R-hat fixture's value is checked in-run against a badly-mixed control)")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/arviz/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/arviz/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/arviz/  (no data staged -- the draws are synthesised)"
