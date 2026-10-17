# The petsc recipe's checks, sourced by 01-ksp.task.json after its sha256 pin is checked.
# Staged rather than inline because spawn carries the task script in EC2 user data (16 KiB, spawn#1063).
echo "== versions, from the running build rather than the env lock =="
# The fem-cfd lock in git reports Built 2026.10.08.014833 while the newest image tag was pushed
# ~13 h later, so the lock is not proof of what is in this image. The committed reference below
# is version-matched, so the version is read from the binary and reported.
python3 -u -c "
from petsc4py import PETSc
import petsc4py, sys
print('version\tpetsc4py %s' % petsc4py.__version__)
print('version\tpetsc %s' % '.'.join(str(v) for v in PETSc.Sys.getVersion()))
" 2>&1 | tee versions.txt
grep -q "petsc4py 3.25" versions.txt || { echo "FAIL: expected petsc4py 3.25.x; the committed reference is version-matched"; exit 1; }

echo "== ex2 serial: reproduce src/ksp/ksp/tutorials/output/ex2_1.out =="
mpiexec -n 1 python3 -u ex2.py ex2 > ex2_serial.txt 2>&1
test "$?" = "0" || { tail -25 ex2_serial.txt; echo "FAIL: serial run failed"; exit 1; }
cat ex2_serial.txt | sed 's/^/  /'

echo "== ex2 on 2 ranks: reproduce output/ex2_2.out =="
mpiexec -n 2 python3 -u ex2.py ex2 > ex2_par.txt 2>&1
test "$?" = "0" || { tail -25 ex2_par.txt; echo "FAIL: 2-rank run failed"; exit 1; }
cat ex2_par.txt | sed 's/^/  /'

echo "== the convergence ladder =="
mpiexec -n 1 python3 -u ex2.py order > order.txt 2>&1
test "$?" = "0" || { tail -25 order.txt; echo "FAIL: order study failed"; exit 1; }
cat order.txt | sed 's/^/  /'

check_run() {
  f="$1"; want_size="$2"; want_its="$3"; want_err="$4"; label="$5"
  SZ=$(awk -F'\t' '$1=="mpi_size"{print $2}' "$f")
  IT=$(awk -F'\t' '$1=="iterations"{print $2}' "$f")
  ER=$(awk -F'\t' '$1=="norm_of_error"{print $2}' "$f")
  CR=$(awk -F'\t' '$1=="converged_reason"{print $2}' "$f")
  RR=$(awk -F'\t' '$1=="unprec_relative_residual"{print $2}' "$f")
  RT=$(awk -F'\t' '$1=="rtol"{print $2}' "$f")
  printf '%s_mpi_size\t%s\n' "$label" "$SZ"
  printf '%s_iterations\t%s\n' "$label" "$IT"
  printf '%s_norm_of_error\t%s\n' "$label" "$ER"
  printf '%s_converged_reason\t%s\n' "$label" "$CR"
  printf '%s_unprec_relative_residual\t%s\n' "$label" "$RR"
  # THE RANK COUNT IS ASSERTED FROM INSIDE THE RUN. conda-forge ships nompi builds of petsc at
  # higher build numbers than the mpi ones, so an unpinned solve can hand back a serial binary
  # that under `mpiexec -n 2` runs two independent rank-0 problems -- both printing the same
  # answer, and a naive parallel-equals-serial check passing vacuously.
  test "$SZ" = "$want_size" || { echo "FAIL: $label reports $SZ ranks, launched $want_size"; return 1; }
  test "$IT" = "$want_its" || { echo "FAIL: $label took $IT iterations, PETSc's committed output says $want_its"; return 1; }
  awk -v g="$ER" -v w="$want_err" 'BEGIN{d=g-w; if(d<0)d=-d; if (d/w > 1e-5) {printf "FAIL: norm of error %s vs committed %s\n", g, w; exit 1}}' || return 1
  # PETSc's own convergence verdict: positive reasons are convergence, negative are divergence.
  # KSP_CONVERGED_RTOL is 2. The unpreconditioned residual is reported alongside, NOT asserted,
  # because it is not the norm GMRES tested.
  test "$CR" -gt 0 || { echo "FAIL: $label did not converge (KSPConvergedReason $CR)"; return 1; }
  printf '%s\treproduces PETSc committed output (%s iterations, error %s)\n' "$label" "$want_its" "$want_err"
}

check_run ex2_serial.txt 1 4 0.000392701 serial || exit 1
check_run ex2_par.txt    2 7 0.000292349 parallel || exit 1
printf 'reference\tboth ex2_1.out and ex2_2.out reproduced at the env version\n'

# ---------------------------------------------------- the theory-fixed rate
# Second-order stencil, so each halving of h must cut the error by ~4. Asserting the RATIO
# tests the discretisation; a tolerance at one resolution would pass for a first-order scheme.
awk '
  /^order_N/ { r=$NF; if (r!="-") { n++; s+=r; if (r<1.9 || r>2.1) bad++ } }
  END{ printf "order_measurements\t%d\n", n+0
       if (n>0) printf "order_mean_p\t%.3f\n", s/n
       printf "order_out_of_band\t%d\n", bad+0
       if (n < 3) { print "FAIL: fewer than 3 order measurements"; exit 1 }
       if (bad+0 > 0) { print "FAIL: a measured order is not ~2 (O(h^2))"; exit 1 } }' order.txt | tee order_check.tsv
test "${PIPESTATUS[0]}" = "0" || { echo "FAIL: convergence order check failed"; exit 1; }
printf 'identity_order\tmeasured convergence order ~2: O(h^2), the stencil theoretical rate\n'

{ printf 'observable\tvalue\n'; cat versions.txt
  awk -F'\t' '{print "serial_"$1"\t"$2}' ex2_serial.txt 2>/dev/null | grep -vE "^serial_version"
  awk -F'\t' '{print "parallel_"$1"\t"$2}' ex2_par.txt 2>/dev/null | grep -vE "^parallel_version"
  cat order_check.tsv; } > score.tsv
cp score.tsv petsc-diag.txt
uname -m | awk '{print "arch\t"$1}'
echo "PETSC OK"
