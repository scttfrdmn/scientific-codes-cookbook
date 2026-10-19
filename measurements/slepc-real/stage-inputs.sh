#!/usr/bin/env bash
# Stage slepc-real: the task's two scripts and their pins. No data: the operator is generated in
# the run and its reference is a closed form.
#
# Validated here, without SLEPc, is the REFERENCE: real.py's closed-form cluster enumeration is
# checked against a dense diagonalisation of the same 7-point operator on small cubes, eigenvalue
# by eigenvalue and multiplicity by multiplicity, and its plan for the production grid is printed.
#
#   bash stage-inputs.sh <bucket>
set -euo pipefail
BUCKET="${1:?pass your bucket}"
REGION="${AWS_REGION:-us-west-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DST="s3://$BUCKET/inputs/slepc-real"

python3 -I - "$HERE/real.py" <<'PY'
import importlib.util, sys
import numpy as np
sp = importlib.util.spec_from_file_location("real", sys.argv[1])
r = importlib.util.module_from_spec(sp); sp.loader.exec_module(r)

def dense(N):
    T = 2 * np.eye(N) - np.eye(N, k=1) - np.eye(N, k=-1)
    I = np.eye(N)
    A = (np.kron(np.kron(T, I), I) + np.kron(np.kron(I, T), I) + np.kron(np.kron(I, I), T))
    return np.linalg.eigvalsh(A)

for N in (9, 12, 14):
    w = dense(N)
    P = r.plan(N, 40, 10)
    ex, _ = r.exact_list(P["clusters"], P["ncl"])
    ex = np.array(ex)
    d = float(np.abs(w[:ex.size] - ex).max())
    nb = int((w < P["b"]).sum())
    print("  N=%2d: %d values in %d clusters, closed form vs eigvalsh worst %.2e, %d below the cut (want %d)"
          % (N, ex.size, P["ncl"], d, nb, P["nev"]))
    if d > 1e-12 or nb != P["nev"]:
        sys.exit("  the closed-form enumeration does not reproduce the dense spectrum")
    # the multiplicities must also be what eigvalsh sees: count dense values per exact cluster
    for v, t, m in P["clusters"][:P["ncl"]]:
        if int((np.abs(w - v) < 1e-9).sum()) != m:
            sys.exit("  cluster %s: multiplicity %d in the closed form, %d dense"
                     % (t, m, int((np.abs(w - v) < 1e-9).sum())))
print("  closed form, multiplicities and the inertia cut all match a dense diagonalisation")

for N in (128,):
    P = r.plan(N, 100, 20)
    cl = P["clusters"]
    gaps = [(cl[i + 1][0] - cl[i][0]) / cl[i][0] for i in range(P["ncl"])]
    print("  N=%d (%d unknowns): NEV %d in %d clusters, LOBPCG %d, inertia cut %.12g and mid %.12g (%d below)"
          % (N, N ** 3, P["nev"], P["ncl"], P["nlob"], P["b"], P["bmid"], P["nmid"]))
    i = int(np.argmin(gaps))
    print("  narrowest cluster gap: %s -> %s, relative %.3e" % (cl[i][1], cl[i + 1][1], gaps[i]))
PY

( cd "$HERE" && { sha256sum real.py real.sh 2>/dev/null || shasum -a 256 real.py real.sh; } > pins.sha256 )
cat "$HERE/pins.sha256"
for f in real.py real.sh pins.sha256; do
  aws s3 cp --region "$REGION" --only-show-errors "$HERE/$f" "$DST/$f"
done
echo "staged to $DST/"
