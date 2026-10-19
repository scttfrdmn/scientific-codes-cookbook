#!/usr/bin/env bash
# measurements/petsc-real: six SuiteSparse SPD matrices, each solved four ways under MPI.
#   run.sh <MPI ranks> <rtol> [matrix names...]
# Runs on the aarch.science fem-cfd image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys, coreutils and awk. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
NP=${1:?ranks}; RTOL=${2:?rtol}; shift 2
export HOME=/tmp TMPDIR=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
# Docker's /dev/shm is 64 MB; Open MPI's shared-memory segments go to host /tmp instead (spawn#1111).
export OMPI_MCA_btl_sm_backing_directory=/tmp
MPI="mpiexec -n $NP --oversubscribe --map-by hwthread --bind-to hwthread"
O=/tmp/out W=/var/tmp/mats
mkdir -p "$O" "$W"
cd /tmp

# name  rows  nonzeros  block size  BoomerAMG profile (audikw_1 is 3-D elasticity, 3 dof per node)
ALL="parabolic_fem 525825 3674625 1 default
apache2 715176 4817870 1 default
ecology2 999999 4995991 1 default
thermal2 1228045 8580313 1 default
G3_circuit 1585478 7660826 1 default
audikw_1 943695 77651847 3 elasticity"

{
  echo "uname_m	$(uname -m)"
  echo "online_cpus	$(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores	$(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  echo "cpu_model	$(grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo | cut -d: -f2)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
  python3 -u - <<'PY' 2>&1
import ctypes, glob, os, sys
# Describes the run only; nothing here may fail it.
try:
    from petsc4py import PETSc
    print("petsc\t%s" % ".".join(str(v) for v in PETSc.Sys.getVersion()))
    import petsc4py
    print("petsc4py\t%s" % str(getattr(petsc4py, "__version__", "unknown")))
    for lib in sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*"))):
        f = ctypes.CDLL(lib).openblas_get_corename
        f.restype = ctypes.c_char_p
        print("openblas_corename\t%s" % f().decode())
        break
except Exception as e:
    print("host_probe_error\t%s" % str(e))
PY
  echo "mpiexec	$(mpiexec --version 2>&1 | head -n 1)"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

echo "== staged inputs must match their pins =="
sha256sum -c /tmp/pins.sha256 || { echo "FAIL: a staged input does not match its pin"; exit 1; }

SEL="$*"
# mpiexec forwards its stdin to rank 0, so every command in the loop reads /dev/null: otherwise the
# first mpiexec swallows the rest of the matrix list and the loop ends after one matrix, exit 0.
WANT=$(echo "$ALL" | awk -v s=" $SEL " 's=="  " || index(s, " "$1" ")' | wc -l)
U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
: > "$O/results.tsv"
: > "$O/times.tsv"
echo "$ALL" | while read -r NAME ROWS NNZ BS HYP; do
  if [ -n "$SEL" ] && ! echo " $SEL " | grep -q " $NAME "; then continue; fi
  echo "== $NAME: convert (serial, not timed as the tool) =="
  T0=$(date +%s)
  python3 -u convert.py "/tmp/mm/$NAME.tar.gz" "$NAME" "$ROWS" "$NNZ" "$W/$NAME.dat" > "$O/convert-$NAME.log" 2>&1 < /dev/null
  RC=$?
  T1=$(date +%s)
  cat "$O/convert-$NAME.log"
  [ "$RC" = 0 ] || { echo "FAIL: convert $NAME exited $RC"; exit 1; }
  echo "== $NAME: solve on $NP ranks =="
  $MPI python3 -u solve.py "$W/$NAME.dat" "$NAME" "$ROWS" "$NNZ" "$BS" "$NP" "$RTOL" "$O/results.tsv" "$HYP" > "$O/solve-$NAME.log" 2>&1 < /dev/null
  RC=$?
  T2=$(date +%s)
  tail -n 60 "$O/solve-$NAME.log"
  printf '%s\tconvert_s\t%d\n%s\tsolve_wall_s\t%d\n' "$NAME" $((T1-T0)) "$NAME" $((T2-T1)) >> "$O/times.tsv"
  [ "$RC" = 0 ] || { echo "FAIL: solve $NAME exited $RC"; exit 1; }
  rm -f "$W/$NAME.dat"
done
RC=$?
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  awk -F'\t' '$2=="solve_wall_s"{s+=$3} $2=="convert_s"{c+=$3} END{printf "total_solve_wall_s\t%d\ntotal_convert_s\t%d\n", s, c}' "$O/times.tsv"
  echo "cgroup_memory_peak_gib	$(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)"
  echo "cpu_seconds	$(awk -v a="$U0" -v b="$U1" 'BEGIN{printf "%.0f", (b-a)/1e6}')"
} | tee -a "$O/times.tsv"
[ "$RC" = 0 ] || exit 1
N=$(awk -F'\t' '$2=="matrix" && $3=="ok"' "$O/results.tsv" | wc -l)
echo "matrices solved and checked: $N"
[ "$N" -eq "$WANT" ] || { echo "FAIL: $N of $WANT matrices completed"; exit 1; }
echo "PETSC REAL OK"
