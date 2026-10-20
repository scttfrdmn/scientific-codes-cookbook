#!/usr/bin/env bash
# measurements/cantera-real: Cantera on GRI-Mech 3.0's own validation set (real.py).
#   run.sh <worker processes>
set +e
set -uo pipefail
NP=${1:?workers}
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
# one thread per worker process
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
W=/tmp/out
mkdir -p $W
cd /tmp

echo "== the staged GRI-Mech 3.0 files and target pages must match their pins =="
( cd /tmp/gri && sha256sum -c /tmp/sources.sha256 ) > $W/sources.check 2>&1
rc=$?
tail -3 $W/sources.check
[ "$rc" = 0 ] || { echo "FAIL: a staged source does not match sources.sha256" | tee $W/check.txt; exit 1; }
echo "all $(wc -l < /tmp/sources.sha256) sources match sources.sha256"

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
for m in ("cantera", "numpy"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
  ldd "$(python3 -c 'import cantera._cantera as c; print(c.__file__)' 2>/dev/null)" 2>/dev/null | grep -iE 'blas|lapack|sundials|mkl' | sed 's/ (0x.*//'
} > $W/host.txt
cat $W/host.txt

T0=$(date +%s)
python3 -u -W ignore::DeprecationWarning /tmp/real.py /tmp/gri $W "$NP" 2>&1 | tee $W/real.log
CHK=${PIPESTATUS[0]}
T1=$(date +%s)
{
  echo "tool_wall_s	$((T1-T0))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -r $CG/cpu.stat ] && awk -v w=$((T1-T0)) '/^usage_usec/{printf "avg_cores_whole_task\t%.2f\n", $2/(w*1e6)}' $CG/cpu.stat
  df -h /tmp /var/tmp | tail -2
} | tee $W/footprint.txt
[ "$CHK" = 0 ] && echo "ALL CHECKS PASSED" || { echo "FAIL: check rc=$CHK"; exit 1; }
