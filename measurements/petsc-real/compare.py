#!/usr/bin/env python3
"""Compare two chips' petsc-real results.tsv.

    python3 -I compare.py <results dir A> <results dir B>

Asserted (exit 1 on failure):
  - the integers are identical: rank count, rows, nonzeros, MUMPS inertia;
  - each solve is the same fixed point: x_A - x_B = (x_A - x*) - (x_B - x*), so for any vector w,
    |x_A.w - x_B.w| <= (||x_A - x*|| + ||x_B - x*||) ||w||, with both forward errors measured in-run.
Reported: iteration counts (a Krylov path), the solver's residuals, forward errors and times.
"""
import os
import sys


def load(d):
    r = {}
    for line in open(os.path.join(d, "results.tsv")):
        f = line.rstrip("\n").split("\t")
        if len(f) == 4:
            r[(f[0], f[1], f[2])] = f[3]
    return r


A, B = load(sys.argv[1]), load(sys.argv[2])
bad = 0
mats = sorted({k[0] for k in A if k[1] == "matrix" and k[2] == "ok"}, key=lambda m: int(A[(m, "matrix", "rows")]))
if mats != sorted({k[0] for k in B if k[1] == "matrix" and k[2] == "ok"}, key=lambda m: int(B[(m, "matrix", "rows")])):
    sys.exit("FAIL: the two runs completed different matrices")
SOLV = ("mumps_ldlt", "superlu_lu", "cg_gamg", "cg_hypre")
print("%-14s %-11s %9s %9s  %-10s %-10s  %-10s  %s" % ("matrix", "solver", "its A", "its B", "fwd err A", "fwd err B",
                                                      "max|dw|/bound", "x.w identical"))
for m in mats:
    for key in ("mpi_size", "rows", "nonzeros"):
        if A[(m, "matrix", key)] != B[(m, "matrix", key)]:
            print("FAIL: %s %s %s vs %s" % (m, key, A[(m, "matrix", key)], B[(m, "matrix", key)]))
            bad += 1
    if A[(m, "mumps_ldlt", "inertia")] != B[(m, "mumps_ldlt", "inertia")]:
        print("FAIL: %s inertia %s vs %s" % (m, A[(m, "mumps_ldlt", "inertia")], B[(m, "mumps_ldlt", "inertia")]))
        bad += 1
    xs = float(A[(m, "matrix", "norm2_xstar")])
    for s in SOLV:
        fa, fb = float(A[(m, s, "fwd_err_2")]), float(B[(m, s, "fwd_err_2")])
        worst, same = 0.0, 0
        for k in (0, 1):
            wn = float(A[(m, "matrix", "norm2_w%d" % k)])
            da = float(A[(m, s, "x_dot_w%d" % k)]) - float(B[(m, s, "x_dot_w%d" % k)])
            bound = (fa + fb) * xs * wn
            worst = max(worst, abs(da) / bound if bound > 0 else (0.0 if da == 0 else float("inf")))
            same += A[(m, s, "x_dot_w%d" % k)] == B[(m, s, "x_dot_w%d" % k)]
        if not bool(worst <= 1.0):
            print("FAIL: %s %s: |x_A.w - x_B.w| exceeds the two forward errors' bound (%.3g)" % (m, s, worst))
            bad += 1
        print("%-14s %-11s %9s %9s  %.3e  %.3e  %.3e     %d/2" % (m, s, A[(m, s, "iterations")], B[(m, s, "iterations")],
                                                             fa, fb, worst, same))
print("cross-chip: %s" % ("FAIL (%d)" % bad if bad else "integers identical; every solve the same fixed point within its measured forward errors"))
sys.exit(1 if bad else 0)
