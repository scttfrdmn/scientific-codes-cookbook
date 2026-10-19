#!/usr/bin/env python3
"""slepc-real: the lowest ~100 modes of a 3-D Laplacian at production size, against its exact spectrum.

    mpiexec -n R python3 -u real.py <N> <nev_target> <ranks> <outdir> [lobpcg_target]

The operator is the 7-point Dirichlet Laplacian on an N x N x N grid (unit spacing), whose
eigenvalues are known exactly for any N:

    lambda(k1,k2,k3) = s(k1) + s(k2) + s(k3),   s(k) = 4 sin^2(k pi / 2(N+1)),   k_d = 1..N

On a cube those are degenerate: every permutation of (k1,k2,k3) is the same eigenvalue, so the
low spectrum is clusters of multiplicity 1, 3 and 6. That is the point: missing a copy of a
repeated eigenvalue is the classic way a Krylov eigensolver goes wrong, and the multiplicities
are known exactly.

What a pass means, all on one run:
 1. Krylov-Schur, shift-and-invert about 0 with a MUMPS Cholesky factorisation -- the way SLEPc is
    used for the low modes of a large FEM/FD operator -- returns NEV eigenpairs, and each computed
    theta_i sits within its own residual norm of the exact lambda_i (for symmetric A and unit x,
    some eigenvalue lies within ||Ax - theta x|| of theta: a theorem, not a tolerance).
 2. Every cluster comes back with exactly its multiplicity.
 3. Sylvester's law of inertia, from an independent LDL^T factorisation of A - b I with b in the
    gap just above the NEV-th eigenvalue, counts exactly NEV eigenvalues below b -- so nothing
    below the cut was missed. A second shift, inside the spectrum, counts the clusters below it.
 4. The NEV eigenvectors are orthonormal (X^T X == I).
 5. A different algorithm with no factorisation -- generalized Davidson preconditioned by
    hypre BoomerAMG --
    finds the lowest clusters again, within the residual bounds of both.
 6. The MPI rank count, read from inside, equals the ranks launched, and rank 0 owns a strict
    subset of the rows.

Run with `python3 -u`; every value printed is str()-ed, so a line that describes the run cannot
kill it.
"""
import itertools
import json
import math
import os
import resource
import sys
import time


# ------------------------------------------------------------- the closed form (no PETSc)
def s1(k, n):
    return 4.0 * math.sin(k * math.pi / (2.0 * (n + 1))) ** 2


def exact_clusters(n, need):
    """The lowest clusters of the N^3 Laplacian, as (value, multiset, multiplicity), sorted,
    covering at least `need` eigenvalues plus one more cluster. Complete by construction: any
    eigenvalue with some k_d > K is >= s(K+1) + 2 s(1), so enumerating 1..K is exhaustive below
    that bound, and K grows until the bound clears the clusters returned."""
    K = 2
    while True:
        cl = sorted((sum(s1(k, n) for k in t), t)
                    for t in itertools.combinations_with_replacement(range(1, K + 1), 3))
        out, cum = [], 0
        for v, t in cl:
            out.append((v, t, len(set(itertools.permutations(t)))))
            if cum >= need:
                break
            cum += out[-1][2]
        if cum >= need and out[-1][0] < s1(K + 1, n) + 2 * s1(1, n):
            break
        K += 1
    # Distinct multisets can give the SAME eigenvalue exactly (a trig identity at rational
    # angles: on N=14, (1,1,5) and (1,3,4) coincide), so a cluster is a value, not a multiset.
    # Merge values equal to rounding; the label lists every multiset in the cluster.
    merged = []
    for v, t, m in out:
        if merged and abs(v - merged[-1][0]) <= 1e-12 * v:
            merged[-1] = (merged[-1][0], merged[-1][1] + (t,), merged[-1][2] + m)
        else:
            merged.append((v, (t,), m))
    return merged


def plan(n, nev_target, lob_target):
    """NEV = the first cluster boundary at or above the target, so the cut never splits a
    degenerate cluster; b = the midpoint of the gap above it; and the same for the second solver."""
    cl = exact_clusters(n, nev_target + 40)
    cum, nev, ncl = 0, None, None
    for i, c in enumerate(cl):
        cum += c[2]
        if cum >= nev_target:
            nev, ncl = cum, i + 1
            break
    b = 0.5 * (cl[ncl - 1][0] + cl[ncl][0])
    mid = ncl // 2
    bmid = 0.5 * (cl[mid - 1][0] + cl[mid][0])
    nmid = sum(c[2] for c in cl[:mid])
    cum, nlob, nlobcl = 0, None, None
    for i, c in enumerate(cl):
        cum += c[2]
        if cum >= lob_target:
            nlob, nlobcl = cum, i + 1
            break
    return dict(clusters=cl, nev=nev, ncl=ncl, b=b, bmid=bmid, nmid=nmid, nlob=nlob, nlobcl=nlobcl)


def exact_list(cl, ncl):
    vals, lab = [], []
    for v, t, m in cl[:ncl]:
        vals += [v] * m
        lab += [t] * m
    return vals, lab


# ------------------------------------------------------------------------------ the run
def main():
    N = int(sys.argv[1])
    NEV_T = int(sys.argv[2])
    RANKS = int(sys.argv[3])
    OUT = sys.argv[4]
    LOB_T = int(sys.argv[5]) if len(sys.argv) > 5 else 20

    import numpy as np
    from petsc4py import PETSc
    import slepc4py
    slepc4py.init([sys.argv[0]])
    from slepc4py import SLEPc

    comm = PETSc.COMM_WORLD
    RANK, SIZE = comm.getRank(), comm.getSize()
    rows, fails = [], []
    t_start = time.time()

    def rec(k, v):
        if RANK == 0:
            line = "%s\t%s" % (str(k), str(v))
            print(line, flush=True)
            rows.append(line)

    def check(name, ok, detail):
        ok = bool(ok)
        rec("CHECK " + name, ("PASS " if ok else "FAIL ") + str(detail))
        if not ok:
            fails.append(name)

    def ver(label, fn):
        try:
            rec(label, fn())
        except Exception as exc:
            rec(label, "unknown (%s)" % type(exc).__name__)

    def phase(name, t0):
        dt = time.time() - t0
        rec("time_%s_s" % name, "%.2f" % dt)
        return dt

    ver("petsc", lambda: ".".join(str(v) for v in PETSc.Sys.getVersion()))
    ver("slepc", lambda: ".".join(str(v) for v in SLEPc.Sys.getVersion()))
    ver("numpy", lambda: np.__version__)

    def corename():
        import ctypes, glob
        for p in sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*"))):
            lib = ctypes.CDLL(p)
            lib.openblas_get_corename.restype = ctypes.c_char_p
            return lib.openblas_get_corename().decode()
        return "no libopenblas in %s/lib" % sys.prefix
    ver("openblas_corename", corename)

    P = plan(N, NEV_T, LOB_T)
    NEV, NCL = P["nev"], P["ncl"]
    n = N ** 3
    rec("operator", "7-point Dirichlet Laplacian, %d^3 grid, %d unknowns" % (N, n))
    rec("nev", "%d eigenvalues in %d clusters (first cluster boundary >= %d)" % (NEV, NCL, NEV_T))
    rec("mpi_ranks", SIZE)
    check("mpi_ranks", SIZE == RANKS, "%d ranks reported, %d launched" % (SIZE, RANKS))

    # -------------------------------------------------------------- assemble, CSR per rank
    t0 = time.time()
    lo = RANK * (n // SIZE) + min(RANK, n % SIZE)
    hi = lo + n // SIZE + (1 if RANK < n % SIZE else 0)
    r = np.arange(lo, hi, dtype=np.int64)
    i, j, k = r % N, (r // N) % N, r // (N * N)
    cols, vals, rr = [r], [np.full(r.size, 6.0)], [r]
    for d, coord, stride in ((0, i, 1), (1, j, N), (2, k, N * N)):
        for sgn in (-1, 1):
            m = (coord + sgn >= 0) & (coord + sgn < N)
            rr.append(r[m]); cols.append(r[m] + sgn * stride); vals.append(np.full(int(m.sum()), -1.0))
    rr = np.concatenate(rr); cc = np.concatenate(cols); vv = np.concatenate(vals)
    order = np.lexsort((cc, rr))
    rr, cc, vv = rr[order], cc[order], vv[order]
    ia = np.concatenate([[0], np.cumsum(np.bincount(rr - lo, minlength=hi - lo))]).astype(PETSc.IntType)
    A = PETSc.Mat().createAIJ(size=((hi - lo, n), (hi - lo, n)),
                              csr=(ia, cc.astype(PETSc.IntType), vv), comm=comm)
    A.assemble()
    del rr, cc, vv, order, r, i, j, k
    phase("assemble", t0)
    olo, ohi = A.getOwnershipRange()
    rec("ownership_rank0", "rows %d..%d of %d" % (olo, ohi, n))
    check("distributed", SIZE == 1 or (ohi - olo) < n,
          "rank 0 owns %d of %d rows across %d ranks" % (ohi - olo, n, SIZE))
    check("symmetric", A.isSymmetric(0.0), "A == A^T exactly")

    # --------------------------------------- 1. Krylov-Schur, shift-and-invert, MUMPS Cholesky
    t0 = time.time()
    eps = SLEPc.EPS().create(comm=comm)
    eps.setOperators(A)
    eps.setProblemType(SLEPc.EPS.ProblemType.HEP)
    eps.setType(SLEPc.EPS.Type.KRYLOVSCHUR)
    eps.setDimensions(nev=NEV)
    eps.setTarget(0.0)
    eps.setWhichEigenpairs(SLEPc.EPS.Which.TARGET_MAGNITUDE)
    eps.setTolerances(tol=1e-10, max_it=500)
    st = eps.getST()
    st.setType(SLEPc.ST.Type.SINVERT)
    ksp = st.getKSP(); ksp.setType("preonly")
    pc = ksp.getPC(); pc.setType("cholesky"); pc.setFactorSolverType("mumps")
    eps.setFromOptions()
    eps.solve()
    t_ks = phase("krylovschur_sinvert", t0)
    nconv = eps.getConverged()
    rec("ks_ncv", eps.getDimensions()[1])
    rec("ks_converged", nconv)
    rec("ks_iterations", eps.getIterationNumber())
    check("ks_converged", nconv >= NEV, "%d of %d requested" % (nconv, NEV))
    if nconv < NEV:
        return finish(rows, fails, OUT, RANK, t_start)

    t0 = time.time()
    X, theta, resid, relerr = [], [], [], []
    y = A.createVecLeft()
    for q in range(nconv):
        x = A.createVecRight()
        th = eps.getEigenpair(q, x).real
        A.mult(x, y); y.axpy(-th, x)
        X.append(x); theta.append(th); resid.append(y.norm() / x.norm())
        relerr.append(eps.computeError(q, SLEPc.EPS.ErrorType.RELATIVE))
    order = sorted(range(nconv), key=lambda q: theta[q])[:NEV]
    X = [X[q] for q in order]; theta = np.array([theta[q] for q in order])
    resid = np.array([resid[q] for q in order]); relerr = np.array([relerr[q] for q in order])
    ex, lab = exact_list(P["clusters"], NCL)
    ex = np.array(ex)
    err = np.abs(theta - ex)
    # Rounding in forming the residual and the closed form: a few ulps of ||A|| <= 12.
    slack = 64 * np.finfo(float).eps * 12.0
    rec("ks_worst_abs_err", "%.3e" % err.max())
    rec("ks_worst_rel_err", "%.3e" % (err / ex).max())
    rec("ks_worst_residual", "%.3e" % resid.max())
    rec("ks_worst_slepc_relerr", "%.3e" % relerr.max())
    check("ks_within_residual_bound", np.all(err <= resid + slack),
          "every |theta_i - lambda_i| <= ||r_i|| + %.1e (worst ratio err/(r+slack) %.3f)"
          % (slack, (err / (resid + slack)).max()))
    # The sharper, quadratic bound for a Rayleigh quotient: |lambda - theta| <= ||r||^2 / delta,
    # delta = the distance from theta to the nearest OTHER distinct exact eigenvalue.
    cvals = np.array([c[0] for c in P["clusters"]])
    delta = np.array([np.min(np.abs(cvals[np.abs(cvals - e) > 1e-12 * e] - t)) for t, e in zip(theta, ex)])
    quad = resid ** 2 / delta
    rec("ks_min_gap_delta", "%.3e" % delta.min())
    check("ks_within_quadratic_bound", np.all(err <= quad + slack),
          "every |theta_i - lambda_i| <= ||r_i||^2/delta_i + %.1e (worst ratio %.4f)"
          % (slack, (err / (quad + slack)).max()))

    # 2. multiplicities: assign each computed value to the nearest exact cluster
    cv = np.array([c[0] for c in P["clusters"]])
    assign = np.array([int(np.argmin(np.abs(cv - t))) for t in theta])
    got = np.bincount(assign, minlength=len(cv))
    want = np.array([c[2] for c in P["clusters"]])
    bad = [(P["clusters"][q][1], int(got[q]), int(want[q])) for q in range(NCL) if got[q] != want[q]]
    extra = int(got[NCL:].sum())
    check("multiplicities", not bad and extra == 0,
          "%d clusters (%s), each with its exact multiplicity%s"
          % (NCL, "x".join(str(m) for m in want[:NCL]),
             "" if not bad else "; mismatched %s" % bad))

    # 4. orthonormality
    G = np.zeros((NEV, NEV))
    for a in range(NEV):
        G[a, a:] = X[a].mDot(X[a:])
    G = G + np.triu(G, 1).T
    orth = float(np.abs(G - np.eye(NEV)).max())
    rec("orthonormality_max_abs", "%.3e" % orth)
    check("orthonormal", orth <= 1e-11, "max |X^T X - I| = %.3e" % orth)
    phase("ks_checks", t0)
    # Free the shift-and-invert factor before the next one: python references to the ST, KSP
    # and PC keep it alive otherwise, and two MUMPS factors at once doubles the footprint.
    del X, G
    pc.reset(); ksp.reset(); st.reset(); eps.reset()
    eps.destroy(); st.destroy(); ksp.destroy(); pc.destroy()
    del eps, st, ksp, pc
    import gc
    gc.collect()
    PETSc.garbage_cleanup()

    # --------------------------------------------- 3. Sylvester inertia, independent LDL^T
    opts = PETSc.Options()
    # PETSc refuses MatGetInertia on a MUMPS factor without these two: ICNTL(13)=1 keeps the root
    # node off ScaLAPACK so the global inertia is correct in parallel, ICNTL(24)=1 is null-pivot
    # detection (so the zero count means something). SLEPc's spectrum slicing sets the same pair.
    opts["mat_mumps_icntl_13"] = 1
    opts["mat_mumps_icntl_24"] = 1
    for tag, b, expect in (("cut", P["b"], NEV), ("mid", P["bmid"], P["nmid"])):
        t0 = time.time()
        B = A.duplicate(copy=True)
        B.shift(-b)
        k2 = PETSc.KSP().create(comm=comm)
        k2.setOperators(B)
        k2.setType("preonly")
        p2 = k2.getPC(); p2.setType("cholesky"); p2.setFactorSolverType("mumps")
        k2.setFromOptions(); k2.setUp()
        nneg, nzero, npos = p2.getFactorMatrix().getInertia()
        phase("inertia_%s" % tag, t0)
        rec("inertia_%s" % tag, "shift %.12g: %d negative, %d zero, %d positive" % (b, nneg, nzero, npos))
        check("inertia_%s" % tag, nneg == expect and nzero == 0 and nneg + npos == n,
              "%d eigenvalues below %.6g, closed form says %d" % (nneg, b, expect))
        p2.reset(); k2.reset(); k2.destroy(); p2.destroy(); B.destroy()
        del p2, k2, B
        gc.collect(); PETSc.garbage_cleanup()

    # ----------------------- 5. a second algorithm with no factorisation, on a multigrid preconditioner
    # ALT picks it: "gd-hypre" (generalized Davidson + BoomerAMG, one vector per PCApply) is the one
    # the measurement runs; the others are kept so the comparison can be repeated.
    ALT = os.environ.get("SLEPC_REAL_ALT", "gd-hypre")
    rec("alt_solver", ALT)
    t0 = time.time()
    NL = P["nlob"]
    e2 = SLEPc.EPS().create(comm=comm)
    e2.setOperators(A)
    e2.setProblemType(SLEPc.EPS.ProblemType.HEP)
    alg, pct = ALT.split("-")
    e2.setType({"gd": SLEPc.EPS.Type.GD, "jd": SLEPc.EPS.Type.JD, "lobpcg": SLEPc.EPS.Type.LOBPCG}[alg])
    e2.setDimensions(nev=NL)
    e2.setWhichEigenpairs(SLEPc.EPS.Which.SMALLEST_REAL)
    e2.setTolerances(tol=1e-9, max_it=20000)
    s2 = e2.getST(); s2.setType(SLEPc.ST.Type.PRECOND)
    k3 = s2.getKSP(); k3.setType("preonly")
    p3 = k3.getPC()
    if pct == "hypre":
        p3.setType("hypre"); p3.setHYPREType("boomeramg")
    else:
        p3.setType("gamg")
    e2.setFromOptions()
    e2.solve()
    phase("alt_solve", t0)
    nc2 = e2.getConverged()
    rec("alt_converged", nc2)
    rec("alt_iterations", e2.getIterationNumber())
    check("alt_converged", nc2 >= NL, "%d of %d requested" % (nc2, NL))
    if nc2 >= NL:
        th2, r2 = [], []
        for q in range(nc2):
            x = A.createVecRight()
            t = e2.getEigenpair(q, x).real
            A.mult(x, y); y.axpy(-t, x)
            th2.append(t); r2.append(y.norm() / x.norm())
        o2 = np.argsort(th2)[:NL]
        th2 = np.array(th2)[o2]; r2 = np.array(r2)[o2]
        e2err = np.abs(th2 - ex[:NL])
        d = np.abs(th2 - theta[:NL])
        rec("alt_worst_abs_err", "%.3e" % e2err.max())
        rec("alt_vs_ks_worst_abs", "%.3e" % d.max())
        check("alt_within_residual_bound", np.all(e2err <= r2 + slack),
              "every second-solver |theta - lambda| <= its own ||r|| (worst ratio %.3f)"
              % (e2err / (r2 + slack)).max())
        check("alt_agrees_with_ks", np.all(d <= r2 + resid[:NL] + 2 * slack),
              "%d values; the two algorithms agree within the sum of their residuals (worst %.3e)"
              % (NL, d.max()))
    e2.destroy()

    if RANK == 0:
        with open(os.path.join(OUT, "eigenvalues.tsv"), "w") as fh:
            fh.write("i\ttheta\texact\tabs_err\tresidual\tcluster\n")
            for q in range(NEV):
                fh.write("%d\t%.17g\t%.17g\t%.3e\t%.3e\t%s\n"
                         % (q + 1, theta[q], ex[q], err[q], resid[q], "+".join("-".join(map(str, t)) for t in lab[q])))
    return finish(rows, fails, OUT, RANK, t_start)


def finish(rows, fails, OUT, RANK, t_start):
    from mpi4py import MPI
    me = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mx = MPI.COMM_WORLD.allreduce(me, op=MPI.MAX)
    tot = MPI.COMM_WORLD.allreduce(me, op=MPI.SUM)
    if RANK == 0:
        rows.append("rank_maxrss_max_mib\t%.0f" % (mx / 1024.0))
        rows.append("rank_maxrss_sum_gib\t%.2f" % (tot / 1048576.0))
        rows.append("time_total_s\t%.2f" % (time.time() - t_start))
        rows.append("checks_failed\t%d%s" % (len(fails), (" " + ",".join(fails)) if fails else ""))
        with open(os.path.join(OUT, "score.tsv"), "w") as fh:
            fh.write("\n".join(rows) + "\n")
        print("\n".join(rows[-3:]), flush=True)
        if not fails:
            print("SLEPC REAL OK", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
