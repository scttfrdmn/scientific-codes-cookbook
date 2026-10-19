#!/usr/bin/env python3
"""Attribution probe: does the cross-chip L2 shift come from FMA contraction in the JIT kernels?

aarch64 has FMA in its baseline ISA, so gcc -O2 contracts a*b+c in the kernels ffcx generates;
baseline x86-64 has no FMA instruction, so the same C compiles without it. Solve the serial
P2 N=64 rung of poisson3d.py twice in one process -- the default JIT flags, then with
-ffp-contract=off -- each in its own JIT cache, and print both L2 errors. If contraction is
the cause, the `off` value on Graviton4 equals the x86 value. Serial: no partitioning effect.
"""
import numpy as np
import ufl
from mpi4py import MPI
from dolfinx import fem, mesh
from dolfinx.fem.petsc import LinearProblem

COMM = MPI.COMM_WORLD
msh = mesh.create_unit_cube(COMM, 64, 64, 64, cell_type=mesh.CellType.tetrahedron)
tdim = msh.topology.dim
msh.topology.create_connectivity(tdim - 1, tdim)
facets = mesh.exterior_facet_indices(msh.topology)
pi = np.pi
for tag, flags in (("default", ["-O2", "-g0"]), ("contract-off", ["-O2", "-g0", "-ffp-contract=off"])):
    jit = {"cffi_extra_compile_args": flags, "cache_dir": "/tmp/jit-" + tag}
    V = fem.functionspace(msh, ("Lagrange", 2))
    x = ufl.SpatialCoordinate(msh)
    u_ex = ufl.sin(ufl.pi * x[0]) * ufl.sin(ufl.pi * x[1]) * ufl.sin(ufl.pi * x[2])
    u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    a = ufl.inner(ufl.grad(u), ufl.grad(v)) * ufl.dx
    L = ufl.inner(-ufl.div(ufl.grad(u_ex)), v) * ufl.dx(metadata={"quadrature_degree": 6})
    ubc = fem.Function(V)
    ubc.interpolate(lambda y: np.sin(pi * y[0]) * np.sin(pi * y[1]) * np.sin(pi * y[2]))
    bc = fem.dirichletbc(ubc, fem.locate_dofs_topological(V, tdim - 1, facets))
    opts = {"ksp_type": "cg", "ksp_norm_type": "unpreconditioned", "ksp_rtol": 1e-12,
            "ksp_atol": 0.0, "ksp_max_it": 2000, "pc_type": "hypre", "pc_hypre_type": "boomeramg"}
    p = LinearProblem(a, L, bcs=[bc], petsc_options_prefix="fc_%s_" % tag.replace("-", ""),
                      petsc_options=opts, jit_options=jit)
    uh = p.solve()
    if isinstance(uh, tuple):
        uh = uh[0]
    e = uh - u_ex
    l2 = np.sqrt(fem.assemble_scalar(fem.form(ufl.inner(e, e) * ufl.dx(metadata={"quadrature_degree": 7}),
                                              jit_options=jit)))
    print("l2_P2_N64_%s\t%.12e\tits %d" % (tag, l2, p.solver.getIterationNumber()), flush=True)
    del p
