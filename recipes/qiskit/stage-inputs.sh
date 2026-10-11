#!/usr/bin/env bash
# Stage the checks. NOTHING SCIENTIFIC IS FETCHED, because every reference here is arithmetic:
# a Bell amplitude is 1/sqrt(2), the QFT is the DFT matrix, <Z> under RY(theta) is cos(theta),
# and Grover on two qubits lands on the marked state. There is no dataset to pin and no
# published table to reproduce -- so this script verifies the closed forms LOCALLY, in a few
# lines, before an instance is paid for. If the arithmetic below were wrong the recipe would
# chase a wrong target on a running box.
set -euo pipefail

BUCKET="s3://${1:?pass your bucket -- make stage RECIPE=qiskit does this}"
REGION="${AWS_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cp "$SCRIPT_DIR/identities.py" .

echo "== the checks must parse =="
python3 -c "import ast,io; ast.parse(io.open('identities.py',encoding='utf-8').read())" \
  || { echo "  identities.py does not parse" >&2; exit 1; }
printf '  %-16s %6s bytes\n' identities.py "$(wc -c < identities.py | tr -d '[:space:]')"

echo "== the closed forms this recipe asserts, derived here with numpy alone =="
python3 - <<'PY'
import sys
import numpy as np

# 1/sqrt(2) must be representable such that the equality assertion is meaningful.
r2 = 1.0 / np.sqrt(2.0)
print("  1/sqrt(2)            = %.20f" % r2)
if r2 * r2 * 2.0 != 1.0:
    print("  note: 2*(1/sqrt2)^2 != 1 exactly, which is expected and not asserted")

# The DFT matrix is the QFT reference. Confirm it is unitary, or the comparison is meaningless.
for n in (2, 3, 4):
    N = 2 ** n
    j, k = np.meshgrid(np.arange(N), np.arange(N), indexing="ij")
    F = np.exp(2j * np.pi * j * k / N) / np.sqrt(N)
    err = np.abs(F.conj().T @ F - np.eye(N)).max()
    print("  DFT n=%d unitary to  %.3e" % (n, err))
    if err > 1e-13:
        sys.exit("  the DFT reference is not unitary -- fix the reference, not the tool")
    # and that the sign conventions really are far apart, so the control below is sound
    if np.abs(F - F.conj()).max() < 0.1:
        sys.exit("  F and its conjugate are too close for n=%d to act as a control" % n)

# <Z> = cos(theta) is the expectation reference.
print("  cos(0), cos(pi/2), cos(pi) = %.1f, %.3e, %.1f"
      % (np.cos(0.0), np.cos(np.pi / 2), np.cos(np.pi)))

# Grover: one iteration on 2 qubits is exact in theory. sin^2((2k+1)asin(1/sqrt(N))) at k=1,N=4.
theta = np.arcsin(1.0 / 2.0)
p = np.sin(3 * theta) ** 2
print("  Grover 2-qubit, 1 iteration, theoretical P = %.17f" % p)
if abs(p - 1.0) > 1e-12:
    sys.exit("  the Grover closed form does not give 1 -- re-derive before asserting it")
print("  all closed forms check out")
PY

sha256sum identities.py > pins.sha256
aws s3 cp --region "$REGION" identities.py "$BUCKET/inputs/qiskit/identities.py" >/dev/null
aws s3 cp --region "$REGION" pins.sha256  "$BUCKET/inputs/qiskit/pins.sha256"  >/dev/null
cat pins.sha256
echo "done."
echo "  $BUCKET/inputs/qiskit/  (no data staged -- every reference is arithmetic)"
