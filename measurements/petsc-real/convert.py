#!/usr/bin/env python3
"""Read one SuiteSparse Matrix Market archive and write it as a PETSc binary matrix (serial).

    python3 -u convert.py <name.tar.gz> <name> <rows> <nonzeros> <out.dat>

Checks the matrix against the collection's published size and nonzero count, and that every
diagonal entry is positive (necessary for positive definiteness). Prints key<TAB>value lines.
"""
import sys
import tarfile
import time

import numpy as np
import scipy.io
import scipy.sparse
from petsc4py import PETSc

tgz, name, rows, nnz, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
t0 = time.time()
with tarfile.open(tgz, "r:gz") as tf:
    A = scipy.io.mmread(tf.extractfile("%s/%s.mtx" % (name, name)))
A = scipy.sparse.csr_matrix(A)
A.sum_duplicates()
A.sort_indices()
t1 = time.time()
d = A.diagonal()
print("convert_read_s\t%.1f" % (t1 - t0))
print("rows\t%d" % A.shape[0])
print("cols\t%d" % A.shape[1])
print("nonzeros\t%d" % A.nnz)
print("explicit_zeros\t%d" % int((A.data == 0).sum()))
print("diag_min\t%.17g" % d.min())
print("structurally_symmetric_and_equal\t%d" % int((A != A.T).nnz == 0))
if A.shape != (rows, rows) or A.nnz != nnz:
    sys.exit("FAIL: %s is %s with %d nonzeros; the collection publishes %d x %d with %d"
             % (name, A.shape, A.nnz, rows, rows, nnz))
if not bool(d.min() > 0):
    sys.exit("FAIL: %s has a non-positive diagonal entry, so it is not positive definite" % name)
M = PETSc.Mat().createAIJ(size=A.shape, csr=(A.indptr.astype(PETSc.IntType), A.indices.astype(PETSc.IntType),
                                              A.data.astype(PETSc.ScalarType)), comm=PETSc.COMM_SELF)
v = PETSc.Viewer().createBinary(out, "w", comm=PETSc.COMM_SELF)
M.view(v)
v.destroy()
print("convert_write_s\t%.1f" % (time.time() - t1))
