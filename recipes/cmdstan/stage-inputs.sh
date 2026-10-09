#!/usr/bin/env bash
# Stage the model, its data and the checks.
#
# WHY THERE IS NO SCIENTIFIC DATA TO FETCH. The point of this recipe is a model whose posterior
# is known in CLOSED FORM, so the "reference" is arithmetic rather than a file: a Beta prior and
# a binomial likelihood are conjugate, so theta | y ~ Beta(a+y, b+N-y) exactly. The data below
# is four integers; everything asserted is derived from them analytically.
#
# cmdstan COMPILES every model with a C++ toolchain at run time. The bayes env carries one
# (g++ 15.3.0, GNU make 4.4.1, verified by probe), and a model compiles in about 10 s.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=cmdstan does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/beta_binomial.stan" "$SCRIPT_DIR/identities.py" .

# N, y and the prior shapes. y=17 of N=50 with a Beta(2,3) prior gives a Beta(19,36) posterior,
# which is well inside (0,1) -- so the logit transform is well conditioned and the sampler is
# not being asked to work near a boundary where the comparison would be about tails instead of
# about the posterior.
cat > data.json <<'JSON'
{ "N": 50, "y": 17, "a": 2.0, "b": 3.0 }
JSON

echo "== the model must be valid Stan before it costs an instance =="
grep -q 'theta ~ beta(a, b);'      beta_binomial.stan || { echo "  prior missing" >&2; exit 1; }
grep -q 'y ~ binomial(N, theta);'  beta_binomial.stan || { echo "  likelihood missing" >&2; exit 1; }
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }

echo "== the data must be the conjugate case the checks assume =="
python3 - <<'PY'
import json, math, sys
d = json.load(open("data.json"))
N, y, a, b = int(d["N"]), int(d["y"]), float(d["a"]), float(d["b"])
if not (0 <= y <= N):
    sys.exit("  y=%d is not in [0,%d]" % (y, N))
if a <= 0 or b <= 0:
    sys.exit("  prior shapes must be positive")
A, B = a + y, b + N - y
mean = A / (A + B)
sd = math.sqrt(A * B / ((A + B) ** 2 * (A + B + 1)))
print("  data        N=%d y=%d prior Beta(%g,%g)" % (N, y, a, b))
print("  posterior   Beta(%g,%g)  mean=%.15f  sd=%.15f" % (A, B, mean, sd))
print("  mode        %.15f (constrained scale)" % ((A - 1) / (A + B - 2)))
# The checks need both exponent pairs to be strictly positive, or the kernel has no interior
# mode and the MAP identities below would compare against a boundary.
if a - 1 + y <= 0 or b - 1 + N - y <= 0:
    sys.exit("  constrained-scale exponents are not positive; no interior mode")
if not (0.05 < mean < 0.95):
    sys.exit("  posterior mean %.3f is too close to a boundary for a clean comparison" % mean)
print("  exponents   constrained (%g,%g) / unconstrained (%g,%g)"
      % (a - 1 + y, b - 1 + N - y, A, B))
PY

FILES="beta_binomial.stan data.json identities.py"
# shellcheck disable=SC2086
shasum -a 256 $FILES > pins.sha256
sed 's/^/  /' pins.sha256
# shellcheck disable=SC2086
for f in $FILES pins.sha256; do
  aws s3 cp "$f" "$BUCKET/inputs/cmdstan/$f" --region "$REGION" --only-show-errors
done
echo "done."
echo "  $BUCKET/inputs/cmdstan/  (conjugate Beta-Binomial; the reference is arithmetic)"
