#!/usr/bin/env python3
"""SLEPc on Graviton4: eigenvalues against a closed form, with the rank count asserted.

The tridiagonal matrix with 2 on the diagonal and -1 off it -- the 1-D Dirichlet Laplacian
stencil -- has eigenvalues

    lambda_k = 4 sin^2( k pi / (2 (n+1)) ),   k = 1..n

exactly. So SLEPc is checked against mathematics rather than against itself, with no
reference data and nothing staged. Three further things make this more than one number:

 * scipy's `eigh_tridiagonal` is a DENSE LAPACK path, wholly unlike SLEPc's Krylov-Schur
   iteration, so it is a genuine second opinion. Measured, scipy sits 1.2e-12 from the
   closed form itself, so the two tools agree to within their own accuracies rather than
   to an arbitrary tolerance.
 * eigenvector orthogonality (X X^T == I) is an exact identity for a symmetric problem.
 * THE RANK COUNT IS ASSERTED FROM INSIDE THE RUN, and so is the ownership range. A
   nompi build under `mpiexec -n 2` would report size 1 twice, or own all 200 rows on
   each rank -- producing the right eigenvalues and a false claim about parallelism.
   Owning rows 0..100 of 200 is what proves the matrix is actually distributed.

Invoked twice by the spec: serially, which writes its eigenvalues to serial.json, then
under `mpiexec -n 2`, which reads that file back and asserts the two agree.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import json
import os
import sys

import numpy as np

try:
    from petsc4py import PETSc
    import slepc4py
    slepc4py.init(sys.argv)
    from slepc4py import SLEPc
except Exception as exc:
    print("FAIL: slepc4py import failed: %s: %s" % (type(exc).__name__, exc))
    sys.exit(1)

RANK = PETSc.COMM_WORLD.getRank()
SIZE = PETSc.COMM_WORLD.getSize()
ROWS = []


def rec(k, v):
    if RANK == 0:
        print("%s\t%s" % (k, v))
        ROWS.append((k, v))


def die(msg):
    if RANK == 0:
        print("FAIL: %s" % msg)
    PETSc.COMM_WORLD.barrier()
    sys.exit(1)


def ver(label, fn):
    """A line whose only job is to describe the run must never be able to fail it.
    PETSc.Sys.getVersion() returns a TUPLE, and "%s" % tuple consumed its elements as
    format arguments -- which killed an earlier probe before a single eigenvalue was
    computed. Hence a helper that degrades instead of raising."""
    try:
        rec(label, fn())
    except Exception as exc:
        rec(label, "unknown (%s)" % type(exc).__name__)


ver("petsc", lambda: ".".join(str(v) for v in PETSc.Sys.getVersion()))
ver("slepc", lambda: ".".join(str(v) for v in SLEPc.Sys.getVersion()))
ver("numpy", lambda: np.__version__)
ver("scalar_type", lambda: PETSc.ScalarType.__name__)

N = 200
NEV = 6
TOL = 1e-12
SERIAL_FILE = "serial.json"

rec("matrix", "tridiagonal [-1,2,-1], n=%d" % N)
rec("eigenpairs_requested", "%d" % NEV)
rec("mpi_ranks", "%d" % SIZE)

# ------------------------------------------------------------------- build the operator
A = PETSc.Mat().create(comm=PETSc.COMM_WORLD)
A.setSizes([N, N])
A.setFromOptions()
A.setUp()
lo, hi = A.getOwnershipRange()
for i in range(lo, hi):
    A[i, i] = 2.0
    if i > 0:
        A[i, i - 1] = -1.0
    if i < N - 1:
        A[i, i + 1] = -1.0
A.assemble()

if not A.isSymmetric(1e-12):
    die("the assembled matrix is not symmetric, so the HEP problem type is wrong")
rec("symmetric", "yes")

# --------- the rank count AND the distribution, which is the part a nompi build fails
rec("ownership_rank%d" % RANK, "rows %d..%d of %d" % (lo, hi, N))
if SIZE > 1:
    owned = hi - lo
    if owned >= N:
        die("rank %d owns all %d rows under %d ranks; this is a serial build running %d "
            "independent copies, not a distributed solve" % (RANK, N, SIZE, SIZE))
    if owned <= 0:
        die("rank %d owns no rows" % RANK)
    rec("distribution", "rank 0 owns %d of %d rows across %d ranks" % (owned, N, SIZE))

# ------------------------------------------------------------------------ solve
eps = SLEPc.EPS().create(comm=PETSc.COMM_WORLD)
eps.setOperators(A)
eps.setProblemType(SLEPc.EPS.ProblemType.HEP)
eps.setWhichEigenpairs(SLEPc.EPS.Which.SMALLEST_REAL)
eps.setDimensions(nev=NEV)
eps.setTolerances(tol=TOL, max_it=5000)
eps.setFromOptions()
eps.solve()

nconv = eps.getConverged()
rec("solver", "%s" % eps.getType())
rec("converged", "%d" % nconv)
rec("iterations", "%d" % eps.getIterationNumber())
if nconv < NEV:
    die("only %d of %d eigenpairs converged" % (nconv, NEV))

xr = A.createVecRight()
vals, resids = [], []
for i in range(NEV):
    vals.append(float(eps.getEigenpair(i, xr).real))
    resids.append(float(eps.computeError(i, SLEPc.EPS.ErrorType.RELATIVE)))
vals = np.array(sorted(vals))
if not np.all(np.isfinite(vals)):
    die("a non-finite eigenvalue")

# ------------------------------------------------- 1. the closed form, exactly known
k = np.arange(1, N + 1)
exact_all = 4.0 * np.sin(k * np.pi / (2.0 * (N + 1))) ** 2
exact = np.sort(exact_all)[:NEV]
rel = np.abs((vals - exact) / exact)
for i in range(NEV):
    rec("lambda_%d" % (i + 1), "%.17g  exact %.17g  rel %.3e"
        % (vals[i], exact[i], rel[i]))
rec("closed_form_worst_rel", "%.3e" % rel.max())
rec("worst_residual", "%.3e" % max(resids))
# The solver was asked for 1e-12; a converged Krylov method lands near that, and the
# bound is set from the request rather than from the observation.
if rel.max() > 1e-11:
    die("the worst eigenvalue is %.3e from 4 sin^2(k pi / 2(n+1)); the solver tolerance "
        "was %.0e" % (rel.max(), TOL))
if max(resids) > 1e-9:
    die("the worst reported residual is %.3e" % max(resids))

# ordering: the k-th smallest computed value must be the k-th smallest closed form
if not np.all(np.diff(vals) > 0):
    die("the eigenvalues are not strictly increasing: %s" % vals)
rec("ordering", "strictly increasing, matched k-for-k to the closed form")

# THE CONTROL: the closed form must discriminate. A different matrix ([-1,3,-1]) has
# eigenvalues shifted by exactly 1, so comparing against the wrong operator must fail.
shifted = exact + 1.0
ctl = float(np.abs((vals - shifted) / shifted).min())
rec("control_wrong_operator_min_rel", "%.3e" % ctl)
if ctl < 1e-3:
    die("the eigenvalues of a [-1,3,-1] matrix are within %.3e of these, so agreement "
        "with the closed form does not identify the operator" % ctl)

# --------------------------- 2. scipy as an independent referee (dense LAPACK vs Krylov)
try:
    from scipy.linalg import eigh_tridiagonal
    w = np.sort(eigh_tridiagonal(np.full(N, 2.0), np.full(N - 1, -1.0),
                                 eigvals_only=True))[:NEV]
    d_sl = float(np.abs((vals - w) / w).max())
    d_sc = float(np.abs((w - exact) / exact).max())
    rec("scipy_vs_closed_form", "%.3e" % d_sc)
    rec("slepc_vs_scipy", "%.3e" % d_sl)
    # Both sit ~1e-12 from exact, so they agree to within their OWN accuracies. A bound
    # tighter than scipy's own error would be asserting more than either tool delivers.
    if d_sl > 1e-10:
        die("SLEPc and scipy differ by %.3e; scipy is itself %.3e from the closed form, "
            "so this is beyond either one's accuracy" % (d_sl, d_sc))
    rec("cross_validation", "Krylov and dense LAPACK agree to %.1e, each ~%.0e from exact"
        % (d_sl, d_sc))
except Exception as exc:
    die("the scipy cross-check could not run: %s: %s" % (type(exc).__name__, exc))

# ------------------------------------------- 3. orthogonality (serial: vectors are local)
if SIZE == 1:
    X = []
    for i in range(NEV):
        v = A.createVecRight()
        eps.getEigenpair(i, v)
        X.append(v.getArray().copy())
    X = np.array(X)
    G = X @ X.T
    off = float(np.abs(G - np.diag(np.diag(G))).max())
    dia = float(np.abs(np.diag(G) - 1.0).max())
    rec("orthogonality_offdiag", "%.3e" % off)
    rec("orthogonality_norm", "%.3e" % dia)
    if off > 1e-12 or dia > 1e-12:
        die("the eigenvectors are not orthonormal (off-diagonal %.3e, |norm-1| %.3e); "
            "a symmetric problem's eigenvectors must be" % (off, dia))
    rec("orthonormal", "X X^T == I to %.1e" % max(off, dia))
else:
    rec("orthogonality", "skipped: eigenvectors are distributed across %d ranks" % SIZE)

# --------------------- 4. serial and parallel must agree, written once and read back
if RANK == 0:
    if SIZE == 1:
        with open(SERIAL_FILE, "w") as fh:
            json.dump({"n": N, "nev": NEV, "vals": vals.tolist()}, fh)
        rec("serial_values_written", SERIAL_FILE)
    else:
        if not os.path.exists(SERIAL_FILE):
            die("%s is missing; the serial pass must run before the parallel one so the "
                "two can be compared" % SERIAL_FILE)
        prev = json.load(open(SERIAL_FILE))
        if prev["n"] != N or prev["nev"] != NEV:
            die("the serial pass solved n=%s nev=%s, this one n=%d nev=%d"
                % (prev["n"], prev["nev"], N, NEV))
        sv = np.array(prev["vals"])
        d = float(np.abs((vals - sv) / sv).max())
        rec("serial_vs_parallel_worst_rel", "%.3e" % d)
        if d > 1e-11:
            die("the %d-rank eigenvalues differ from the serial ones by %.3e; the same "
                "operator must give the same spectrum however it is distributed"
                % (SIZE, d))
        rec("serial_vs_parallel", "agree to %.1e across %d ranks" % (d, SIZE))

if RANK == 0:
    tag = "serial" if SIZE == 1 else "mpi%d" % SIZE
    with open("score-%s.tsv" % tag, "w") as fh:
        fh.write("observable\tvalue\n")
        for kk, vv in ROWS:
            fh.write("%s\t%s\n" % (kk, vv))
    print("SLEPC OK (%s)" % tag)
