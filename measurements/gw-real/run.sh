#!/usr/bin/env bash
# measurements/gw-real: PyCBC over the GWOSC hour (search.py), then Bilby full PE of GW150914 (pe.py).
#   run.sh <processes> <nlive>
set +e
set -uo pipefail
NP=${1:?processes}
NLIVE=${2:?nlive}
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl
# one BLAS/OpenMP thread per worker process: both phases parallelise over processes
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
W=/tmp/out
mkdir -p $W /tmp/work
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
  python3 -u - <<'PY' 2>&1
import ctypes, glob, os, sys
# Describes the run only; nothing here may fail it.
try:
    for lib in sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*"))):
        try:
            f = ctypes.CDLL(lib).openblas_get_corename
            f.restype = ctypes.c_char_p
            print("openblas_corename\t%s (%s)" % (f().decode(), os.path.basename(lib)))
            break
        except Exception as e:
            print("openblas_corename\tunreadable: %s" % e)
    else:
        print("openblas_corename\tnone in %s/lib" % sys.prefix)
except Exception as e:
    print("openblas_corename\terror: %s" % e)
PY
} > $W/host.txt
cat $W/host.txt

phase() {  # phase <name> <command...>: wall time and the children's peak RSS
  local name=$1; shift
  local t0=$(date +%s)
  "$@"
  local rc=$?
  echo "${name}_wall_s	$(( $(date +%s) - t0 ))" >> $W/phases.txt
  echo "${name}_rc	$rc" >> $W/phases.txt
  return $rc
}
: > $W/phases.txt
phase search bash -c "python3 -u /tmp/search.py /tmp/data $W $NP 2>&1 | grep --line-buffered -v Warning | tee $W/search.log; exit \${PIPESTATUS[0]}"
S=$?
phase pe bash -c "python3 -u /tmp/pe.py /tmp/data $W $NP $NLIVE 2>&1 | grep --line-buffered -v 'Warning\|deprecated behaviour' | tee $W/pe.log; exit \${PIPESTATUS[0]}"
P=$?
{
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  df -h /tmp | tail -1
} | tee $W/footprint.txt
cat $W/phases.txt
[ "$S" = 0 ] && grep -q 'SEARCH OK' $W/search.log || { echo "FAIL: search rc=$S"; exit 1; }
[ "$P" = 0 ] || { echo "FAIL: pe rc=$P"; exit 1; }
echo "ALL CHECKS PASSED"
