#!/usr/bin/env python3
"""Compare two chips' slepc-real outputs.

    python3 -I compare.py <results/chipA/N128> <results/chipB/N128>

The eigenvalues are a fixed point: each chip's theta_i is within its own residual of the exact
lambda_i, so the two chips must agree within the sum of their residuals -- asserted. Iteration
counts and the residuals themselves are the path, and are reported, not asserted.
"""
import sys


def load(d):
    ev = []
    with open(d + "/eigenvalues.tsv") as fh:
        next(fh)
        for line in fh:
            f = line.rstrip("\n").split("\t")
            ev.append((float(f[1]), float(f[4]), f[5]))
    sc = {}
    with open(d + "/score.tsv") as fh:
        for line in fh:
            k, _, v = line.rstrip("\n").partition("\t")
            sc[k] = v
    return ev, sc


a, sa = load(sys.argv[1])
b, sb = load(sys.argv[2])
fail = []
if len(a) != len(b) or any(x[2] != y[2] for x, y in zip(a, b)):
    fail.append("the two chips returned different cluster assignments")
d = [abs(x[0] - y[0]) for x, y in zip(a, b)]
bound = [x[1] + y[1] + 2 * 64 * 2.220446049250313e-16 * 12 for x, y in zip(a, b)]
worst = max(range(len(d)), key=lambda i: d[i] / bound[i])
print("eigenvalues            %d each; max |dtheta| %.3e; worst ratio to residual bound %.3f"
      % (len(d), max(d), d[worst] / bound[worst]))
if any(x > y for x, y in zip(d, bound)):
    fail.append("an eigenvalue differs across chips by more than both residuals allow")
print("bit-identical values   %d of %d" % (sum(1 for x in d if x == 0.0), len(d)))
for k in ("inertia_cut", "inertia_mid"):
    same = sa.get(k) == sb.get(k)
    print("%-22s %s" % (k, "identical" if same else "DIFFERENT: %s | %s" % (sa.get(k), sb.get(k))))
    if not same:
        fail.append(k)
for k in ("ks_iterations", "ks_worst_residual", "alt_iterations", "alt_vs_ks_worst_abs",
          "orthonormality_max_abs", "openblas_corename"):
    print("%-22s %s | %s   (path, reported)" % (k, sa.get(k), sb.get(k)))
print("FAIL: " + "; ".join(fail) if fail else "fixed point agrees")
sys.exit(1 if fail else 0)
