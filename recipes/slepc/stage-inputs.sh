#!/usr/bin/env bash
# Stage the checks only. Nothing is fetched: the operator is a tridiagonal stencil whose
# eigenvalues have a closed form, so the reference is mathematics.
#
# Validated here is ONLY the closed form itself and that it discriminates -- no SLEPc.
# The instance is the verdict.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=slepc does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"
grep -q 'getOwnershipRange' identities.py \
  || { echo "  the distribution check is gone; a nompi build would pass silently" >&2; exit 1; }
echo "  ownership-range check present (a nompi build would otherwise pass)"

echo "== the closed form must be right, and must discriminate =="
python3 - <<'PY'
import ast, io, sys
import numpy as np

src = io.open("identities.py", encoding="utf-8").read()
K = {}
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        if node.targets[0].id in ("N", "NEV", "TOL"):
            try:
                K[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
for k in ("N", "NEV"):
    if k not in K:
        sys.exit("  identities.py no longer defines %s" % k)
N, NEV = K["N"], K["NEV"]
print("  n = %d, %d eigenpairs, solver tolerance %s" % (N, NEV, K.get("TOL")))

# Build the matrix densely and diagonalise it independently of any formula, then check
# the closed form against that. This validates the FORMULA, not the solver.
A = np.diag(np.full(N, 2.0)) + np.diag(np.full(N - 1, -1.0), 1) + np.diag(np.full(N - 1, -1.0), -1)
w = np.sort(np.linalg.eigvalsh(A))
k = np.arange(1, N + 1)
closed = np.sort(4.0 * np.sin(k * np.pi / (2.0 * (N + 1))) ** 2)
worst = float(np.abs((closed - w) / w).max())
print("  closed form vs numpy.linalg.eigvalsh over all %d values: worst rel %.3e" % (N, worst))
if worst > 1e-10:
    sys.exit("  the closed form does not reproduce the matrix's spectrum; the formula is wrong")

print("  smallest %d closed-form values:" % NEV)
for i in range(NEV):
    print("    lambda_%d = %.17g" % (i + 1, closed[i]))

# The eigenvalues must be well separated at the low end, or "the k-th smallest" is
# ambiguous and a solver returning them in a different order would still pass.
gaps = np.diff(closed[:NEV + 1])
print("  consecutive gaps among the smallest: %s" % np.array2string(gaps, precision=6))
if (gaps <= 0).any():
    sys.exit("  the low eigenvalues are not strictly separated")
rel_gap = float((gaps / closed[:NEV]).min())
print("  smallest relative gap %.4f -- distinct enough to order unambiguously" % rel_gap)
if rel_gap < 0.1:
    sys.exit("  eigenvalues are nearly degenerate; ordering would be ambiguous")

# And the control the run uses: a [-1,3,-1] matrix shifts every eigenvalue by exactly 1.
shift_rel = float(np.abs(((closed[:NEV] + 1.0) - closed[:NEV]) / closed[:NEV]).min())
print("  a [-1,3,-1] operator differs by at least %.1f relative -- a usable control" % shift_rel)
if shift_rel < 1.0:
    sys.exit("  the control operator is too close to distinguish")
print("  references are sound: a verified closed form, separated spectrum, live control")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/slepc/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256   "$BUCKET/inputs/slepc/pins.sha256"   >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/slepc/  (no data staged -- the reference is a closed form)"
