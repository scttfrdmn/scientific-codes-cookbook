"""Which BoomerAMG settings let CG converge on audikw_1 (3-D elasticity, block size 3)?

A one-off probe on Graviton4 (r8g.4xlarge, 16 ranks); output in results/hypre-probe.txt. Not part of
the verifying run: it chose the `elasticity` profile that solve.py uses for audikw_1.

    mpiexec -n 16 python3 -u hypre_probe.py <audikw_1.dat written by convert.py>
"""
import sys, time
from petsc4py import PETSc
import numpy as np
path = sys.argv[1]
comm = PETSc.COMM_WORLD
opts = PETSc.Options()
opts["matload_block_size"] = 3
A = PETSc.Mat().create(comm=comm); A.setType("aij"); A.setFromOptions()
v = PETSc.Viewer().createBinary(path, "r", comm=comm); A.load(v)
xs = A.createVecRight(); lo, hi = xs.getOwnershipRange()
rng = np.random.default_rng(lo); xs.setArray(rng.random(hi - lo) - 0.5)
b = A.createVecLeft(); A.mult(xs, b); bn = b.norm()
VARIANTS = {
  "default": {},
  "st05": {"pc_hypre_boomeramg_strong_threshold": 0.5},
  "st07": {"pc_hypre_boomeramg_strong_threshold": 0.7},
  "l1sym": {"pc_hypre_boomeramg_relax_type_all": "l1scaled-SOR/Jacobi"},
  "hmis_st07": {"pc_hypre_boomeramg_coarsen_type": "HMIS", "pc_hypre_boomeramg_interp_type": "ext+i",
                "pc_hypre_boomeramg_P_max": 4, "pc_hypre_boomeramg_strong_threshold": 0.7},
  "hmis_st07_l1": {"pc_hypre_boomeramg_coarsen_type": "HMIS", "pc_hypre_boomeramg_interp_type": "ext+i",
                "pc_hypre_boomeramg_P_max": 4, "pc_hypre_boomeramg_strong_threshold": 0.7,
                "pc_hypre_boomeramg_relax_type_all": "l1scaled-SOR/Jacobi"},
  "nodal": {"pc_hypre_boomeramg_nodal_coarsen": 6, "pc_hypre_boomeramg_vec_interp_variant": 3,
            "pc_hypre_boomeramg_strong_threshold": 0.7},
}
for name, o in VARIANTS.items():
    pre = "v%s_" % name
    opts[pre + "pc_hypre_type"] = "boomeramg"
    for k, val in o.items():
        opts[pre + k] = val
    ksp = PETSc.KSP().create(comm=comm); ksp.setOptionsPrefix(pre); ksp.setOperators(A)
    ksp.setType("cg"); ksp.getPC().setType("hypre")
    ksp.setNormType(PETSc.KSP.NormType.UNPRECONDITIONED); ksp.setTolerances(rtol=1e-8, atol=0, max_it=5000)
    ksp.setFromOptions()
    x = A.createVecRight()
    try:
        comm.barrier(); t0 = time.time(); ksp.setUp(); t1 = time.time(); ksp.solve(b, x); t2 = time.time()
        r = A.createVecLeft(); A.mult(x, r); r.aypx(-1, b); e = x.copy(); e.axpy(-1, xs)
        PETSc.Sys.Print("%-14s reason %3d its %5d setup %6.1f solve %6.1f relres %.3e fwd %.3e" % (
            name, ksp.getConvergedReason(), ksp.getIterationNumber(), t1 - t0, t2 - t1, r.norm() / bn, e.norm() / xs.norm()))
    except Exception as ex:
        PETSc.Sys.Print("%-14s error %s" % (name, str(ex).splitlines()[0]))
    ksp.destroy()
