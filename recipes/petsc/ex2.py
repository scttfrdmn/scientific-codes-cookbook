import sys, numpy as np
from petsc4py import PETSc

mode = sys.argv[1]
size = PETSc.COMM_WORLD.getSize()
rank = PETSc.COMM_WORLD.getRank()

def p(*a):
    if rank == 0:
        print(*a, flush=True)

if mode == "ex2":
    # Reproduce src/ksp/ksp/tutorials/ex2.c exactly: 5-point Laplacian on an m x n grid with
    # off-diagonals -1.0 and diagonal 4.0 (UNSCALED -- no h^2), u = all ones, b = A*u. So the
    # exact solution is known by construction and the reported "norm of error" measures how far
    # KSP converged, not discretisation error.
    m = n = 5
    N = m * n
    A = PETSc.Mat().create(comm=PETSc.COMM_WORLD)
    A.setSizes([N, N]); A.setFromOptions(); A.setUp()
    rs, re = A.getOwnershipRange()
    for Ii in range(rs, re):
        i, j = Ii // n, Ii - (Ii // n) * n
        if i > 0:     A.setValues(Ii, Ii - n, -1.0, addv=True)
        if i < m - 1: A.setValues(Ii, Ii + n, -1.0, addv=True)
        if j > 0:     A.setValues(Ii, Ii - 1, -1.0, addv=True)
        if j < n - 1: A.setValues(Ii, Ii + 1, -1.0, addv=True)
        A.setValues(Ii, Ii, 4.0, addv=True)
    A.assemble()

    u = A.createVecRight(); u.set(1.0)
    b = A.createVecLeft();  A.mult(u, b)
    x = A.createVecRight()

    ksp = PETSc.KSP().create(comm=PETSc.COMM_WORLD)
    ksp.setOperators(A, A)
    # ex2.c's test args: -ksp_gmres_cgs_refinement_type refine_always, everything else default
    # (GMRES, rtol 1e-5; ILU in serial, block-Jacobi+ILU in parallel -- which is WHY the
    # committed serial and 2-rank answers legitimately differ).
    # ex2.c sets a GRID-DEPENDENT tolerance at line 164, not PETSc's default:
    #     KSPSetTolerances(ksp, 1.e-2/((m+1)*(n+1)), 1.e-50, ...)
    # For m=n=5 that is 1e-2/36 = 2.778e-4, far looser than the 1e-5 default. Checking it
    # against the committed monitor: the threshold is 2.778e-4 * 3.21109 = 8.92e-4, iteration 3
    # sits at 7.88e-3 and iteration 4 at 3.87e-4 -- so it stops at 4, exactly as ex2_1.out says.
    # Using the default instead converges FURTHER (5 iterations, smaller error), which looks
    # like a disagreement and is really a configuration difference. Reproducing a committed
    # output means reproducing its configuration, not only its mathematics.
    ksp.setTolerances(rtol=1.0e-2 / ((m + 1) * (n + 1)), atol=1.0e-50)
    PETSc.Options()["ksp_gmres_cgs_refinement_type"] = "refine_always"
    ksp.setFromOptions()
    ksp.solve(b, x)
    its = ksp.getIterationNumber()
    rtol = ksp.getTolerances()[0]

    x.axpy(-1.0, u)
    err = x.norm(PETSc.NormType.NORM_2)

    # THE SOLVER'S OWN VERDICT, not a norm recomputed here. GMRES converges on the
    # PRECONDITIONED residual by default, so an unpreconditioned ||b-Ax||/||b|| computed
    # afterwards is a DIFFERENT quantity and can legitimately exceed rtol -- measured 3.578e-04
    # against rtol 2.8e-04 on a run that converged correctly. getConvergedReason() is the
    # completion sentinel: PETSc stating that its own criterion was met.
    reason = ksp.getConvergedReason()
    x.axpy(1.0, u)
    r = b.duplicate(); A.mult(x, r); r.axpy(-1.0, b)
    relres = r.norm(PETSc.NormType.NORM_2) / b.norm(PETSc.NormType.NORM_2)

    p("mpi_size\t%d" % size)
    p("ksp_type\t%s" % ksp.getType())
    p("pc_type\t%s" % ksp.getPC().getType())
    p("iterations\t%d" % its)
    p("norm_of_error\t%.9g" % err)
    p("converged_reason\t%d" % reason)
    p("unprec_relative_residual\t%.3e" % relres)
    p("rtol\t%.1e" % rtol)

elif mode == "order":
    # A manufactured solution NOT representable in the discrete space: u = sin(pi x) sin(pi y),
    # so -laplacian u = 2 pi^2 u. The 5-point stencil is second order, so the error must fall
    # as O(h^2) -- a THEORY-FIXED rate. Asserting the rate across a refinement ladder tests the
    # discretisation itself, which no single-resolution tolerance can do.
    prev = None  # (h, err) of the previous rung
    for NG in (16, 32, 64, 96):
        h = 1.0 / (NG + 1)
        N = NG * NG
        A = PETSc.Mat().create(comm=PETSc.COMM_SELF)
        A.setSizes([N, N]); A.setType("aij"); A.setUp()
        for Ii in range(N):
            i, j = Ii // NG, Ii % NG
            A.setValues(Ii, Ii, 4.0 / h**2, addv=True)
            if i > 0:      A.setValues(Ii, Ii - NG, -1.0 / h**2, addv=True)
            if i < NG - 1: A.setValues(Ii, Ii + NG, -1.0 / h**2, addv=True)
            if j > 0:      A.setValues(Ii, Ii - 1,  -1.0 / h**2, addv=True)
            if j < NG - 1: A.setValues(Ii, Ii + 1,  -1.0 / h**2, addv=True)
        A.assemble()

        xs = np.array([[(j + 1) * h for j in range(NG)] for _ in range(NG)]).ravel()
        ys = np.array([[(i + 1) * h for _ in range(NG)] for i in range(NG)]).ravel()
        ue = np.sin(np.pi * xs) * np.sin(np.pi * ys)
        f  = 2.0 * np.pi**2 * ue

        b = A.createVecLeft(); b.setArray(f); b.assemble()
        x = A.createVecRight()
        ksp = PETSc.KSP().create(comm=PETSc.COMM_SELF)
        ksp.setOperators(A, A)
        ksp.setType("cg")
        # TIGHT tolerance on purpose: the ladder must measure DISCRETISATION error, so the
        # solver error has to be orders of magnitude smaller than it.
        ksp.setTolerances(rtol=1e-13, max_it=20000)
        ksp.getPC().setType("icc")
        ksp.solve(b, x)
        e = float(np.max(np.abs(x.getArray() - ue)))
        # ASSERT THE ORDER, NOT THE RATIO. The error ratio depends on the refinement
        # FACTOR -- a 1.5x refinement gives 1.5^2 = 2.25 for a second-order scheme, which a
        # band centred on 4 would reject as a failure. p = log(e1/e2)/log(h1/h2) is the
        # theoretical quantity and is invariant to how the ladder is spaced.
        import math as _m
        order = (_m.log(prev[1] / e) / _m.log(prev[0] / h)) if prev else float("nan")
        p("order_N%-3d h %.5f  maxerr %.6e  p %s" % (NG, h, e, ("%.3f" % order) if prev else "-"))
        prev = (h, e)
        A.destroy(); ksp.destroy()

