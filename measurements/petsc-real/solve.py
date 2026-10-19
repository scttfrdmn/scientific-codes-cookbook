#!/usr/bin/env python3
"""Solve A x = b for one SuiteSparse SPD matrix with four independent solver stacks, under MPI.

    mpiexec -n <ranks> python3 -u solve.py <matrix.dat> <name> <rows> <nonzeros> <block size> <ranks> <rtol> <out.tsv> <hypre>

The right-hand side is b = A x* for a planted x* (a hash of the global row index, so it is the same
vector at any rank count), so the true error of every solve is known. The four stacks:

  mumps_ldlt     MUMPS symmetric-indefinite LDL^T (PCCHOLESKY, SYM=2): the factor's inertia must be (0, 0, n)
  superlu_lu     SuperLU_DIST LU, a second, unrelated direct code
  cg_gamg        CG + PETSc's own algebraic multigrid
  cg_hypre       CG + hypre BoomerAMG

CG converges on the UNPRECONDITIONED residual, so its stopping test is about ||b - Ax||, the
quantity recomputed afterwards. Rank 0 writes <name> <solver> <key> <value> rows to out.tsv.
"""
import resource
import sys
import time

import numpy as np
from mpi4py import MPI
from petsc4py import PETSc

path, name = sys.argv[1], sys.argv[2]
rows, nnz, bs, want_ranks = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
rtol, out, hyp = float(sys.argv[7]), sys.argv[8], sys.argv[9]
comm = PETSc.COMM_WORLD
mcomm = comm.tompi4py()
rank, size = comm.getRank(), comm.getSize()
EPS = np.finfo(np.float64).eps
rec = []


def put(solver, key, val):
    rec.append("%s\t%s\t%s\t%s" % (name, solver, key, str(val)))


def fail(msg):
    if rank == 0:
        print("FAIL: %s: %s" % (name, msg), flush=True)
        with open(out, "a") as fh:
            fh.write("\n".join(rec) + "\n")
    comm.barrier()
    sys.exit(1)


def hashvec(vec, seed):
    """splitmix64 of (global row index + seed), mapped to [-0.5, 0.5): the same vector at any rank count."""
    lo, hi = vec.getOwnershipRange()
    with np.errstate(over="ignore"):
        z = np.arange(lo, hi, dtype=np.uint64) + np.uint64(seed) * np.uint64(0x9E3779B97F4A7C15)
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        z = z ^ (z >> np.uint64(31))
    vec.setArray((z >> np.uint64(11)).astype(np.float64) / 2.0 ** 53 - 0.5)
    return vec


put("matrix", "mpi_size", size)
# THE RANK COUNT IS ASSERTED FROM INSIDE THE RUN: a nompi petsc under mpiexec would run `size`
# independent rank-0 copies, each printing a plausible answer.
if size != want_ranks:
    fail("PETSc reports %d ranks, launched %d" % (size, want_ranks))

opts = PETSc.Options()
if bs > 1:
    opts["matload_block_size"] = bs
A = PETSc.Mat().create(comm=comm)
A.setType("aij")
A.setFromOptions()
v = PETSc.Viewer().createBinary(path, "r", comm=comm)
t0 = time.time()
A.load(v)
v.destroy()
put("matrix", "load_s", "%.2f" % (time.time() - t0))
gnnz = int(A.getInfo(PETSc.Mat.InfoType.GLOBAL_SUM)["nz_used"])
put("matrix", "rows", A.getSize()[0])
put("matrix", "nonzeros", gnnz)
put("matrix", "block_size", A.getBlockSize())
if A.getSize() != (rows, rows) or gnnz != nnz:
    fail("loaded %s with %d nonzeros; the collection publishes %d x %d with %d" % (A.getSize(), gnnz, rows, rows, nnz))
lo, hi = A.getOwnershipRange()
put("matrix", "rows_per_rank_min", mcomm.allreduce(hi - lo, op=MPI.MIN))
anorm1 = A.norm(PETSc.NormType.NORM_1)
put("matrix", "norm1_A", "%.17g" % anorm1)

xs = hashvec(A.createVecRight(), 1)
b = A.createVecLeft()
A.mult(xs, b)
bnorm = b.norm(PETSc.NormType.NORM_2)
xsnorm = xs.norm(PETSc.NormType.NORM_2)
w = [hashvec(A.createVecRight(), s) for s in (2, 3)]
put("matrix", "norm2_b", "%.17g" % bnorm)
put("matrix", "norm2_xstar", "%.17g" % xsnorm)
for k, wk in enumerate(w):
    put("matrix", "norm2_w%d" % k, "%.17g" % wk.norm(PETSc.NormType.NORM_2))

PREFIX = {"mumps_ldlt": "mumps_", "superlu_lu": "superlu_", "cg_gamg": "gamg_", "cg_hypre": "hypre_"}
SOLVERS = [
    ("mumps_ldlt", "preonly", "cholesky", "mumps"),
    ("superlu_lu", "preonly", "lu", "superlu_dist"),
    ("cg_gamg", "cg", "gamg", None),
    ("cg_hypre", "cg", "hypre", None),
]
opts["mumps_mat_mumps_icntl_13"] = 1   # PETSc needs this for a correct global inertia in parallel
opts["mumps_mat_mumps_icntl_24"] = 1   # and null-pivot detection, so the zero count is real
opts["hypre_pc_hypre_type"] = "boomeramg"
put("cg_hypre", "profile", hyp)
if hyp == "elasticity":
    # PETSc's BoomerAMG defaults (Falgout coarsening, hybrid SOR smoothing) give CG an indefinite
    # preconditioner on 3-D elasticity (KSP_DIVERGED_INDEFINITE_PC at iteration 2). The l1-scaled
    # smoother is SPD by construction; HMIS + ext+i + 0.7 is hypre's 3-D/systems setting.
    for k, val in (("relax_type_all", "l1scaled-SOR/Jacobi"), ("coarsen_type", "HMIS"),
                   ("interp_type", "ext+i"), ("P_max", 4), ("strong_threshold", 0.7)):
        opts["hypre_pc_hypre_boomeramg_" + k] = val
sol = {}
for tag, ktype, ptype, ftype in SOLVERS:
    ksp = PETSc.KSP().create(comm=comm)
    ksp.setOptionsPrefix(PREFIX[tag])
    ksp.setOperators(A)
    ksp.setType(ktype)
    pc = ksp.getPC()
    pc.setType(ptype)
    if ftype:
        pc.setFactorSolverType(ftype)
    else:
        ksp.setNormType(PETSc.KSP.NormType.UNPRECONDITIONED)
        ksp.setTolerances(rtol=rtol, atol=0.0, max_it=20000)
    ksp.setFromOptions()
    x = A.createVecRight()
    comm.barrier()
    t0 = time.time()
    ksp.setUp()
    comm.barrier()
    t1 = time.time()
    ksp.solve(b, x)
    comm.barrier()
    t2 = time.time()
    reason = ksp.getConvergedReason()
    put(tag, "setup_s", "%.2f" % (t1 - t0))
    put(tag, "solve_s", "%.2f" % (t2 - t1))
    put(tag, "converged_reason", reason)
    put(tag, "iterations", ksp.getIterationNumber())
    if ftype == "mumps":
        neg, zero, pos = pc.getFactorMatrix().getInertia()
        put(tag, "inertia", "%d,%d,%d" % (neg, zero, pos))
    r = A.createVecLeft()
    A.mult(x, r)
    r.aypx(-1.0, b)                      # r = b - A x
    relres = r.norm(PETSc.NormType.NORM_2) / bnorm
    # LAPACK's dpot02/dget02 test ratio, ||b - Ax||_1 / (||A||_1 ||x||_1 eps), passed below 30 (THRESH)
    lapack = r.norm(PETSc.NormType.NORM_1) / (anorm1 * x.norm(PETSc.NormType.NORM_1) * EPS)
    e = x.copy()
    e.axpy(-1.0, xs)
    put(tag, "true_relres", "%.6e" % relres)
    put(tag, "ksp_rnorm_rel", "%.6e" % (ksp.getResidualNorm() / bnorm) if not ftype else "-")
    put(tag, "lapack_ratio", "%.4g" % lapack)
    put(tag, "fwd_err_2", "%.6e" % (e.norm(PETSc.NormType.NORM_2) / xsnorm))
    put(tag, "fwd_err_inf", "%.6e" % (e.norm(PETSc.NormType.NORM_INFINITY) / xs.norm(PETSc.NormType.NORM_INFINITY)))
    put(tag, "norm2_x", "%.17g" % x.norm(PETSc.NormType.NORM_2))
    for k, wk in enumerate(w):
        put(tag, "x_dot_w%d" % k, "%.17g" % x.dot(wk))
    sol[tag] = x
    ksp.destroy()
    if not reason > 0:
        fail("%s did not converge (KSPConvergedReason %d)" % (tag, reason))
    if ftype == "mumps" and (neg, zero, pos) != (0, 0, rows):
        fail("MUMPS LDL^T inertia is (%d, %d, %d): the collection says positive definite, so (0, 0, %d)" % (neg, zero, pos, rows))
    if ftype and not bool(lapack < 30):
        fail("%s is not backward stable: LAPACK test ratio %.3g >= 30" % (tag, lapack))
    if not ftype and not bool(relres <= rtol):
        fail("%s reports convergence at rtol %g but ||b-Ax||/||b|| = %.3e" % (tag, rtol, relres))

for tag in ("superlu_lu", "cg_gamg", "cg_hypre"):
    d = sol[tag].copy()
    d.axpy(-1.0, sol["mumps_ldlt"])
    put(tag, "rel_diff_to_mumps", "%.6e" % (d.norm(PETSc.NormType.NORM_2) / xsnorm))

rss = mcomm.allreduce(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
rmax = mcomm.allreduce(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, op=MPI.MAX)
put("matrix", "ru_maxrss_sum_gib", "%.2f" % (rss / 1048576.0))
put("matrix", "ru_maxrss_max_rank_gib", "%.2f" % (rmax / 1048576.0))
put("matrix", "ok", 1)
if rank == 0:
    with open(out, "a") as fh:
        fh.write("\n".join(rec) + "\n")
    print("\n".join(rec), flush=True)
