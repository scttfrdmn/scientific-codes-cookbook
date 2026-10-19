#!/usr/bin/env python3
"""DOLFINx at production size: 3D Poisson by manufactured solution, solved the way a large FEM
run is solved (CG + algebraic multigrid over MPI), with the checks a bug cannot fake.

  CONVERGENCE ORDER. u = sin(pi x) sin(pi y) sin(pi z) on the unit cube, f = -lap(u) derived in
  UFL. On a refinement ladder the L2 error must fall as h^(p+1) and the H1 seminorm error as h^p:
  P2 at 3 and 2, P1 at 2 and 1. The finest P2 rung is N = 128 (16.8 M dofs, 12.6 M tetrahedra).

  CONTAINMENT. u = 1 + x^2 + 2y^2 + 3z^2 lies in P2 and P3, so their Galerkin solution IS u (a
  direct solve, to round-off); P1 cannot represent it -- the control.

  TWO PRECONDITIONERS, ONE DISCRETE SOLUTION. The finest P2 system is solved twice, with hypre
  BoomerAMG and with PETSc GAMG -- unrelated multigrid codebases, the same CG stopping rule on
  the TRUE residual. The FE solution is the fixed point of the discrete system, so the two must
  agree, and their difference measures the algebraic error directly: it must be << the
  discretisation error, or the measured order would be the solver's and not the method's.

  RANKS. MPI.COMM_WORLD.size must equal the ranks launched; the serial pass's errors must equal
  the parallel pass's on every shared rung.

Run with `python3 -u`. Env: EXPECTED_RANKS, P1_LADDER, P2_LADDER (space-separated N), CROSS=1 to
run the GAMG solve on the finest P2 rung, OUT (directory), TAG (serial|par).
"""
import gc
import json
import os
import platform
import resource
import sys
import time

T_START = time.time()

import numpy as np
from mpi4py import MPI

import basix
import dolfinx
import petsc4py
import ufl
from dolfinx import fem, mesh
from dolfinx.fem.petsc import LinearProblem

COMM = MPI.COMM_WORLD
SIZE = COMM.size
ROOT = COMM.rank == 0
OUT = os.environ.get("OUT", "/tmp/out")
TAG = os.environ.get("TAG", "par")
P1_LADDER = [int(n) for n in os.environ.get("P1_LADDER", "16 32 64 128").split()]
P2_LADDER = [int(n) for n in os.environ.get("P2_LADDER", "16 32 64 128").split()]
CROSS = os.environ.get("CROSS", "1") == "1"

EXACT_TOL = 1e-10      # containment: direct solve, so round-off on a ~1-size solution
P1_FLOOR = 1e-4        # P1 must be clearly worse, or containment is not what is tested
ORDER_TOL = 0.05       # finest ratio vs theory
RTOL = 1e-12           # CG stop on ||b - Ax|| / ||b||, both preconditioners
ALG_FRACTION = 1e-3    # algebraic error (BoomerAMG vs GAMG) must be < this x discretisation error
CROSS_TOL = 1e-6       # serial vs parallel, relative, on discretisation errors 1e-2..1e-7

if ROOT:
    os.makedirs(OUT, exist_ok=True)
out = {}


def rec(k, v):
    v = str(v)
    out[k] = v
    if ROOT:
        print("%s\t%s" % (k, v), flush=True)


def dump():
    if ROOT:
        with open(os.path.join(OUT, "score-%s.tsv" % TAG), "w") as fh:
            fh.write("observable\tvalue\n")
            for k, v in out.items():
                fh.write("%s\t%s\n" % (k, v))


def die(msg):
    """MPI_Abort, not a barrier: a collective on a path one rank takes deadlocks the job."""
    dump()
    sys.stderr.write("FAIL [rank %d]: %s\n" % (COMM.rank, msg))
    sys.stderr.flush()
    sys.stdout.flush()
    COMM.Abort(1)


def safe(fn, default="unknown"):
    """A line whose only job is to describe the run must never be able to fail it."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return "%s (%s)" % (default, type(e).__name__)


def corename():
    import ctypes
    import glob
    libs = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
    lib = ctypes.CDLL(libs[0])
    lib.openblas_get_corename.restype = ctypes.c_char_p
    return lib.openblas_get_corename().decode()


def cpu_model():
    txt = open("/proc/cpuinfo").read()
    for key in ("model name", "CPU part"):
        for line in txt.splitlines():
            if line.startswith(key):
                return "%s: %s" % (key, line.split(":", 1)[1].strip())
    return "unknown"


def threads_per_core():
    s = open("/sys/devices/system/cpu/cpu0/topology/thread_siblings_list").read().strip()
    n = 0
    for part in s.split(","):
        a, _, b = part.partition("-")
        n += (int(b) - int(a) + 1) if b else 1
    return n


rec("pass", TAG)
rec("dolfinx", getattr(dolfinx, "__version__", "unknown"))
rec("basix", getattr(basix, "__version__", "unknown"))
rec("ufl", getattr(ufl, "__version__", "unknown"))
rec("petsc4py", getattr(petsc4py, "__version__", "unknown"))
rec("mpi_library", safe(lambda: MPI.Get_library_version().split(",")[0].strip()))
rec("machine", platform.machine())
rec("cpu", safe(cpu_model))
rec("threads_per_core", safe(threads_per_core))
rec("host_cpus", os.cpu_count())
rec("openblas_corename", safe(corename))

launched = int(os.environ.get("EXPECTED_RANKS", str(SIZE)))
rec("mpi_comm_world_size", SIZE)
rec("mpi_ranks_launched", launched)
if SIZE != launched:
    die("launched %d ranks but MPI.COMM_WORLD.size is %d" % (launched, SIZE))

ROWS = []
ROW_KEYS = ["pass", "case", "solver", "degree", "n", "dofs", "cells", "its", "reason",
            "l2_error", "h1_error", "t_mesh", "t_setup", "t_solve", "t_error"]

SOLVERS = {
    "lu": {"ksp_type": "preonly", "pc_type": "lu", "pc_factor_mat_solver_type": "mumps"},
    "boomeramg": {"ksp_type": "cg", "ksp_norm_type": "unpreconditioned", "ksp_rtol": RTOL,
                  "ksp_atol": 0.0, "ksp_max_it": 2000, "pc_type": "hypre",
                  "pc_hypre_type": "boomeramg"},
    "gamg": {"ksp_type": "cg", "ksp_norm_type": "unpreconditioned", "ksp_rtol": RTOL,
             "ksp_atol": 0.0, "ksp_max_it": 2000, "pc_type": "gamg"},
}


def build(n):
    t = time.time()
    msh = mesh.create_unit_cube(COMM, n, n, n, cell_type=mesh.CellType.tetrahedron)
    COMM.Barrier()
    return msh, time.time() - t


def solve(msh, t_mesh, n, degree, case, solver, exact_np, exact_ufl, keep=False):
    t0 = time.time()
    V = fem.functionspace(msh, ("Lagrange", degree))
    x = ufl.SpatialCoordinate(msh)
    u_ex = exact_ufl(x)
    u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    qd = {"quadrature_degree": 2 * degree + 2}
    a = ufl.inner(ufl.grad(u), ufl.grad(v)) * ufl.dx
    # f derived symbolically from u, so the two cannot drift apart
    L = ufl.inner(-ufl.div(ufl.grad(u_ex)), v) * ufl.dx(metadata=qd)
    tdim = msh.topology.dim
    msh.topology.create_connectivity(tdim - 1, tdim)
    facets = mesh.exterior_facet_indices(msh.topology)
    dofs = fem.locate_dofs_topological(V, tdim - 1, facets)
    ubc = fem.Function(V)
    ubc.interpolate(exact_np)
    bc = fem.dirichletbc(ubc, dofs)
    prefix = "%s_p%d_n%d_" % (solver, degree, n)
    problem = LinearProblem(a, L, bcs=[bc], petsc_options_prefix=prefix,
                            petsc_options=SOLVERS[solver])
    COMM.Barrier()
    t1 = time.time()
    uh = problem.solve()
    if isinstance(uh, tuple):
        uh = uh[0]
    COMM.Barrier()
    t2 = time.time()
    ksp = problem.solver
    its, reason = ksp.getIterationNumber(), ksp.getConvergedReason()
    # error integrals one order above the solve's quadrature: the error is what is measured
    qe = {"quadrature_degree": 2 * degree + 3}
    e = uh - u_ex
    l2 = np.sqrt(COMM.allreduce(fem.assemble_scalar(fem.form(ufl.inner(e, e) * ufl.dx(metadata=qe))), op=MPI.SUM))
    h1 = np.sqrt(COMM.allreduce(fem.assemble_scalar(fem.form(ufl.inner(ufl.grad(e), ufl.grad(e)) * ufl.dx(metadata=qe))), op=MPI.SUM))
    if case == "quadratic":
        # grad(e) is degree p-1, so degree 2(p-1) integrates |grad e|^2 exactly: reported beside
        # the 2p+3 figure so a quadrature-scheme effect can be told from a solution one
        ql = {"quadrature_degree": max(2 * (degree - 1), 1)}
        h1l = np.sqrt(COMM.allreduce(fem.assemble_scalar(fem.form(ufl.inner(ufl.grad(e), ufl.grad(e)) * ufl.dx(metadata=ql))), op=MPI.SUM))
        rec("quadratic_P%d_H1_error_q%d_vs_q%d" % (degree, ql["quadrature_degree"], qe["quadrature_degree"]),
            "%.6e %.6e" % (h1l, h1))
    t3 = time.time()
    ndofs = V.dofmap.index_map.size_global * V.dofmap.index_map_bs
    ncells = msh.topology.index_map(tdim).size_global
    row = {"pass": TAG, "case": case, "solver": solver, "degree": degree, "n": n, "dofs": ndofs,
           "cells": ncells, "its": its, "reason": reason, "l2_error": "%.12e" % l2,
           "h1_error": "%.12e" % h1, "t_mesh": "%.2f" % t_mesh, "t_setup": "%.2f" % (t1 - t0),
           "t_solve": "%.2f" % (t2 - t1), "t_error": "%.2f" % (t3 - t2)}
    ROWS.append(row)
    if ROOT:
        print("rung\t" + "\t".join("%s=%s" % (k, row[k]) for k in ROW_KEYS), flush=True)
    if reason <= 0:
        die("%s P%d N=%d did not converge (reason %d, %d its)" % (solver, degree, n, reason, its))
    kept = uh.copy() if keep else None
    del problem, uh
    gc.collect()
    return float(l2), float(h1), kept, V


def write_rows():
    if ROOT:
        with open(os.path.join(OUT, "rungs-%s.tsv" % TAG), "w") as fh:
            fh.write("\t".join(ROW_KEYS) + "\n")
            for r in ROWS:
                fh.write("\t".join(str(r[k]) for k in ROW_KEYS) + "\n")


# ---- 1. containment, direct solve -------------------------------------------------------
quad_np = lambda x: 1.0 + x[0] ** 2 + 2.0 * x[1] ** 2 + 3.0 * x[2] ** 2
quad_ufl = lambda x: 1 + x[0] ** 2 + 2 * x[1] ** 2 + 3 * x[2] ** 2
msh, tm = build(8)
quad = {}
for deg in (1, 2, 3):
    quad[deg] = solve(msh, tm, 8, deg, "quadratic", "lu", quad_np, quad_ufl)[0]
    rec("quadratic_P%d_L2_error" % deg, "%.6e" % quad[deg])
del msh
for deg in (2, 3):
    if not bool(quad[deg] < EXACT_TOL):
        die("P%d contains the quadratic but gave %.3e" % (deg, quad[deg]))
if not bool(quad[1] > P1_FLOOR):
    die("P1 gave %.3e, not worse than %.0e -- containment is not what is tested" % (quad[1], P1_FLOOR))
rec("identity_containment", "P2 %.2e, P3 %.2e exact; P1 control %.3e (%.1ex worse)"
    % (quad[2], quad[3], quad[1], quad[1] / max(quad[2], 1e-300)))

# ---- 2. the convergence ladders, CG + BoomerAMG -----------------------------------------
pi = np.pi
sin_np = lambda x: np.sin(pi * x[0]) * np.sin(pi * x[1]) * np.sin(pi * x[2])
sin_ufl = lambda x: ufl.sin(ufl.pi * x[0]) * ufl.sin(ufl.pi * x[1]) * ufl.sin(ufl.pi * x[2])
ladders = {}
finest = None
for deg, ladder in ((1, P1_LADDER), (2, P2_LADDER)):
    l2s, h1s = [], []
    for n in ladder:
        msh, tm = build(n)
        last = (deg == 2 and n == ladder[-1] and CROSS)
        l2, h1, kept, V = solve(msh, tm, n, deg, "sine", "boomeramg", sin_np, sin_ufl, keep=last)
        l2s.append(l2)
        h1s.append(h1)
        if last:
            finest = (msh, tm, n, kept, l2, h1)
        else:
            del msh
        gc.collect()
        write_rows()
    o2 = [float(np.log2(l2s[i] / l2s[i + 1])) for i in range(len(l2s) - 1)]
    o1 = [float(np.log2(h1s[i] / h1s[i + 1])) for i in range(len(h1s) - 1)]
    ladders[deg] = {"n": ladder, "l2": l2s, "h1": h1s, "l2_orders": o2, "h1_orders": o1}
    rec("sine_P%d_N" % deg, " ".join(str(n) for n in ladder))
    rec("sine_P%d_L2" % deg, " ".join("%.6e" % e for e in l2s))
    rec("sine_P%d_H1" % deg, " ".join("%.6e" % e for e in h1s))
    rec("sine_P%d_L2_orders" % deg, " ".join("%.4f" % o for o in o2))
    rec("sine_P%d_H1_orders" % deg, " ".join("%.4f" % o for o in o1))
    if len(ladder) >= 2:
        for name, orders, theory in (("L2", o2, deg + 1), ("H1", o1, deg)):
            if not bool(abs(orders[-1] - theory) <= ORDER_TOL):
                die("P%d %s finest order %.4f, theory %d" % (deg, name, orders[-1], theory))
        for name, errs in (("L2", l2s), ("H1", h1s)):
            if not all(bool(b < a) for a, b in zip(errs, errs[1:])):
                die("P%d %s error not monotone: %s" % (deg, name, errs))
if all(len(ladders[d]["n"]) >= 2 for d in (1, 2)):
    rec("identity_convergence_order",
        "P1 L2 %.4f H1 %.4f (theory 2, 1); P2 L2 %.4f H1 %.4f (theory 3, 2), finest rungs"
        % (ladders[1]["l2_orders"][-1], ladders[1]["h1_orders"][-1],
           ladders[2]["l2_orders"][-1], ladders[2]["h1_orders"][-1]))

# ---- 3. the second preconditioner on the finest P2 rung ---------------------------------
if finest is not None:
    msh, tm, n, u_amg, l2a, h1a = finest
    l2g, h1g, u_gamg, V = solve(msh, tm, n, 2, "sine", "gamg", sin_np, sin_ufl, keep=True)
    write_rows()
    # both live on the same mesh and element, so their difference is a function on one space
    Vd = u_gamg.function_space
    ua = fem.Function(Vd)
    ua.x.array[:] = u_amg.x.array
    d = ua - u_gamg
    diff = np.sqrt(COMM.allreduce(fem.assemble_scalar(fem.form(ufl.inner(d, d) * ufl.dx)), op=MPI.SUM))
    rec("finest_P2_dofs", ROWS[-1]["dofs"])
    rec("finest_P2_L2_boomeramg", "%.12e" % l2a)
    rec("finest_P2_L2_gamg", "%.12e" % l2g)
    rec("finest_P2_L2_relative_diff", "%.3e" % (abs(l2a - l2g) / l2a))
    rec("finest_P2_algebraic_L2", "%.3e" % diff)
    rec("finest_P2_algebraic_fraction", "%.3e" % (diff / l2a))
    if not bool(diff < ALG_FRACTION * l2a):
        die("BoomerAMG and GAMG solutions differ by %.3e, not << the %.3e discretisation error"
            % (diff, l2a))
    rec("identity_two_preconditioners",
        "BoomerAMG and GAMG reach one discrete solution: ||u_amg - u_gamg|| = %.2e, %.1e of the "
        "discretisation error %.3e" % (diff, diff / l2a, l2a))

# ---- 4. serial vs parallel --------------------------------------------------------------
if ROOT:
    with open(os.path.join(OUT, "ladders-%s.json" % TAG), "w") as fh:
        json.dump({str(k): v for k, v in ladders.items()}, fh)
if TAG == "par" and ROOT:
    sp = os.path.join(OUT, "ladders-serial.json")
    if not os.path.exists(sp):
        die("no serial results at %s" % sp)
    ser = json.load(open(sp))
    worst, shared = 0.0, 0
    for deg in ("1", "2"):
        smap = dict(zip(ser[deg]["n"], zip(ser[deg]["l2"], ser[deg]["h1"])))
        for n, l2, h1 in zip(ladders[int(deg)]["n"], ladders[int(deg)]["l2"], ladders[int(deg)]["h1"]):
            if n in smap:
                shared += 1
                worst = max(worst, abs(smap[n][0] - l2) / l2, abs(smap[n][1] - h1) / h1)
    rec("serial_vs_parallel_rungs", shared)
    rec("serial_vs_parallel_worst_relative", "%.3e" % worst)
    if shared == 0:
        die("no rung shared between the serial and parallel passes")
    if not bool(worst < CROSS_TOL):
        die("serial and %d-rank errors differ by %.3e relative" % (SIZE, worst))
    rec("identity_serial_equals_parallel", "%d rungs, worst %.2e relative (bound %.0e)"
        % (shared, worst, CROSS_TOL))

# ---- footprint ----------------------------------------------------------------------------
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20   # KiB -> GiB on Linux
rec("rss_sum_of_rank_peaks_gib", "%.2f" % COMM.allreduce(rss, op=MPI.SUM))
rec("rss_max_rank_peak_gib", "%.2f" % COMM.allreduce(rss, op=MPI.MAX))
rec("wall_s", "%.1f" % (time.time() - T_START))
write_rows()
dump()
if ROOT:
    print("FENICSX-REAL OK (pass=%s comm_size=%d)" % (TAG, SIZE), flush=True)
