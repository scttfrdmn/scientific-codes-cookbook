#!/usr/bin/env python3
"""DOLFINx on Graviton: an exact solution, a theoretical convergence order, and a real MPI check.

Finite elements admit the two strongest check shapes this project has, and both are available
here without any reference data:

  CONTAINMENT IS AN EQUALITY. If the FEM space contains the exact solution, the Galerkin solution
  IS that solution. u = 1 + x^2 + 2y^2 is a quadratic, so P2 and P3 recover it to solver
  precision -- an equality, not a discretisation band. P1 cannot represent it and returns
  8.2e-03, which is kept as the control: it shows the P2 result comes from containment and not
  from the solver being good.

  A DISCRETISED METHOD HAS A THEORETICAL ORDER. For u = sin(pi x) sin(pi y) the L2 error falls as
  O(h^(p+1)) -- second order for P1, third for P2. Three successive ratios per degree test the
  METHOD; a single resolution would wave through a correct-but-first-order implementation.

NOTE ON THE FENICS TUTORIAL: it uses P1 with this same quadratic and reports ~1e-15. That is a
superconvergence property of the VERTEX values, not the L2 error -- measured here, P1's L2 error
is 8.2e-03. Asserting the tutorial's figure as an L2 bound would be asserting a quirk.

DOLFINx 0.11 API: LinearProblem requires a keyword-only `petsc_options_prefix`. Omitting it
raises TypeError, and the failed __init__ then makes LinearProblem.__del__ raise
AttributeError('_solver') on top of it, so the real error arrives buried. Dirichlet data is set
from a numpy callable rather than fem.Expression, whose interpolation-points API moves between
releases.

Run with `python3 -u`. Run once serially and once under `mpiexec -n 2`: the second invocation
also cross-checks against the first.
"""
import json
import os
import sys

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
EXACT_TOL = 1e-12        # P2/P3 contain the quadratic, so this is solver precision
P1_FLOOR = 1e-4          # P1 must be clearly WORSE, or containment is not what is being tested
ORDER_TOL = 0.05         # how close the finest ratio must sit to the theoretical order
# Serial vs 2-rank, on discretisation errors of 1e-3..1e-6. Mesh partitioning changes the
# assembly summation order, so the last bits legitimately differ: measured 2.434e-09 relative.
# A first version asserted 1e-10, inferred from the probe printing only 5 significant figures --
# which hid exactly that difference and hung the job on a false failure. The bound below is
# justified against the quantity instead: at 1e-6 the two solutions agree to six figures on a
# number that approximates the exact solution to about 1e-3, so partitioning cannot move any
# conclusion drawn from it. The measured value is reported, not just the verdict.
CROSS_TOL = 1e-6

out = {}


def rec(k, v):
    out[k] = v
    if ROOT:
        print("%s\t%s" % (k, v))


def dump():
    if not ROOT:
        return
    with open("/tmp/score.tsv", "w") as fh:
        fh.write("observable\tvalue\n")
        for k, v in out.items():
            fh.write("%s\t%s\n" % (k, v))


def die(msg):
    """Fail the whole job, from any single rank.

    A first version called COMM.Barrier() here. Only rank 0 ever reached it -- rank 1 was
    already past that point -- so the barrier never completed and the job hung for 22 minutes
    until the instance was terminated. Barrier is a COLLECTIVE: it cannot appear on a path that
    one rank takes and another does not. MPI_Abort is the primitive for "stop everything now".
    """
    dump()
    sys.stderr.write("FAIL [rank %d]: %s\n" % (COMM.rank, msg))
    sys.stderr.flush()
    sys.stdout.flush()
    COMM.Abort(1)


rec("dolfinx", dolfinx.__version__)
rec("basix", basix.__version__)
rec("ufl", ufl.__version__)
rec("petsc4py", petsc4py.__version__)
rec("mpi_library", MPI.Get_library_version().split(",")[0])

# ---- the MPI rank count, read from inside the run ----------------------------------------
# conda-forge ships nompi builds at higher build numbers than the MPI ones, so an unpinned
# solve can hand back a serial binary that under `mpiexec -n 2` runs two independent rank-0
# problems -- both printing the right answer, and a naive "parallel == serial" check passing
# vacuously. The count the library itself reports is what rules that out.
launched = int(os.environ.get("EXPECTED_RANKS", str(SIZE)))
rec("mpi_comm_world_size", SIZE)
rec("mpi_ranks_launched", launched)
if SIZE != launched:
    die("launched %d ranks but MPI.COMM_WORLD.size is %d" % (launched, SIZE))
rec("identity_rank_count", "MPI.COMM_WORLD.size == %d, the rank count launched" % SIZE)


def solve(n, degree, exact_np, exact_ufl):
    """-lap(u) = f on the unit square with u = exact on the boundary. Returns the L2 error."""
    msh = mesh.create_unit_square(COMM, n, n)
    V = fem.functionspace(msh, ("Lagrange", degree))
    x = ufl.SpatialCoordinate(msh)
    u_ex = exact_ufl(x)
    u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    a = ufl.inner(ufl.grad(u), ufl.grad(v)) * ufl.dx
    # The source term is derived symbolically from the exact solution, so f and u cannot drift
    # apart -- the method of manufactured solutions rather than a hand-typed f.
    L = ufl.inner(-ufl.div(ufl.grad(u_ex)), v) * ufl.dx

    msh.topology.create_connectivity(msh.topology.dim - 1, msh.topology.dim)
    facets = mesh.exterior_facet_indices(msh.topology)
    dofs = fem.locate_dofs_topological(V, msh.topology.dim - 1, facets)
    ubc = fem.Function(V)
    ubc.interpolate(exact_np)
    bc = fem.dirichletbc(ubc, dofs)

    # A direct LU solve, so "exact" below is a statement about the FEM space rather than about
    # an iterative tolerance.
    problem = LinearProblem(a, L, bcs=[bc], petsc_options_prefix="ck_",
                            petsc_options={"ksp_type": "preonly", "pc_type": "lu"})
    uh = problem.solve()
    e2 = fem.assemble_scalar(fem.form(ufl.inner(uh - u_ex, uh - u_ex) * ufl.dx))
    return float(np.sqrt(COMM.allreduce(e2, op=MPI.SUM)))


# ---- 1. containment: P2 and P3 hold the quadratic, P1 does not ---------------------------
quad_np = lambda x: 1.0 + x[0] ** 2 + 2.0 * x[1] ** 2
quad_ufl = lambda x: 1 + x[0] ** 2 + 2 * x[1] ** 2
quad = {}
for deg in (1, 2, 3):
    quad[deg] = solve(8, deg, quad_np, quad_ufl)
    rec("quadratic_P%d_L2_error" % deg, "%.6e" % quad[deg])
for deg in (2, 3):
    if quad[deg] >= EXACT_TOL:
        die("P%d contains the quadratic but gave %.3e, over %.0e"
            % (deg, quad[deg], EXACT_TOL))
rec("identity_containment_exact",
    "P2 and P3 recover u = 1 + x^2 + 2y^2 to %.2e and %.2e -- the space contains it, so this "
    "is an equality rather than a band" % (quad[2], quad[3]))
if quad[1] <= P1_FLOOR:
    die("P1 gave %.3e, below the %.0e floor -- if P1 is also 'exact' the check is not testing "
        "containment" % (quad[1], P1_FLOOR))
rec("identity_containment_control",
    "P1 cannot represent it and gives %.3e, %.0fx worse than P2 -- which is what shows the "
    "exactness comes from containment, not from the solver"
    % (quad[1], quad[1] / max(quad[2], 1e-300)))

# ---- 2. the convergence ladder ------------------------------------------------------------
sin_np = lambda x: np.sin(np.pi * x[0]) * np.sin(np.pi * x[1])
sin_ufl = lambda x: ufl.sin(ufl.pi * x[0]) * ufl.sin(ufl.pi * x[1])
ladders = {}
for deg in (1, 2):
    errs = [solve(n, deg, sin_np, sin_ufl) for n in (8, 16, 32, 64)]
    orders = [float(np.log2(errs[i] / errs[i + 1])) for i in range(len(errs) - 1)]
    ladders[deg] = {"errors": errs, "orders": orders}
    rec("sin_P%d_errors" % deg, " ".join("%.4e" % e for e in errs))
    rec("sin_P%d_orders" % deg, " ".join("%.3f" % o for o in orders))
    theory = deg + 1
    if abs(orders[-1] - theory) > ORDER_TOL:
        die("P%d's finest order is %.3f, theory says %d" % (deg, orders[-1], theory))
    if not all(e2 < e1 for e1, e2 in zip(errs, errs[1:])):
        die("P%d's error does not fall monotonically: %s" % (deg, errs))
rec("identity_convergence_order",
    "P1 converges at %.3f and P2 at %.3f over 8->64, against theory 2 and 3 -- the ratio "
    "tests the method, which a single resolution cannot"
    % (ladders[1]["orders"][-1], ladders[2]["orders"][-1]))

# ---- 3. the parallel cross-check ---------------------------------------------------------
# Written on every invocation; the 2-rank run reads the serial file back and compares. The
# discretisation errors are 1e-3..1e-6, far above roundoff, so they must agree tightly -- the
# exact cases are at 1e-14 and legitimately differ in their last bits with a different
# assembly order, so they are NOT part of this comparison.
res_path = "/tmp/res_%d.json" % SIZE
if ROOT:
    with open(res_path, "w") as fh:
        json.dump({"quad": {str(k): v for k, v in quad.items()},
                   "ladders": {str(k): v for k, v in ladders.items()}}, fh)
    rec("results_written", res_path)

if SIZE > 1 and ROOT:
    serial_path = "/tmp/res_1.json"
    if not os.path.exists(serial_path):
        die("no serial results at %s -- run the serial pass first" % serial_path)
    ser = json.load(open(serial_path))
    worst = 0.0
    for deg in ("1", "2"):
        for a, b in zip(ser["ladders"][deg]["errors"], ladders[int(deg)]["errors"]):
            worst = max(worst, abs(a - b) / abs(a))
    rec("serial_vs_parallel_worst_relative_diff", "%.3e" % worst)
    rec("serial_vs_parallel_margin",
        "%.0fx inside the %.0e bound" % (CROSS_TOL / max(worst, 1e-300), CROSS_TOL))
    if worst > CROSS_TOL:
        die("serial and %d-rank discretisation errors differ by %.3e relative" % (SIZE, worst))
    rec("identity_serial_equals_parallel",
        "every discretisation error agrees between serial and %d ranks to %.1e relative -- the "
        "rank-count assertion above is what proves the parallelism it cross-validates actually "
        "happened" % (SIZE, worst))

dump()
if ROOT:
    print("FENICSX OK (comm_size=%d)" % SIZE)
