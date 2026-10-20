#!/usr/bin/env bash
# measurements/pyscf-real: one tool per task.
#   run.sh pyscf <workers> <max heavy atoms> <max heavy atoms for CCSD(T)> [probe]
#   run.sh psi4  <workers> <max heavy atoms> <max heavy atoms for CCSD(T)> [probe]   (reads /tmp/in/pyscf.jsonl)
set +e
set -uo pipefail
TOOL=${1:?pyscf or psi4}; NW=${2:?workers}; HA=${3:?max heavy atoms}; CC=${4:?ccsd(t) heavy atoms}; MODE=${5:-real}
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
# one thread per worker process (a BLAS thread per core under 16 workers is oversubscription)
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
W=/tmp/out
mkdir -p $W
cd /tmp

echo "== the staged QM9 files must match the sha256 recorded when they were fetched =="
( cd /tmp/qm9 && grep -E 'dsgdb9nsd.xyz.tar.bz2|uncharacterized.txt' /tmp/qm9.sha256 | sha256sum -c - ) > $W/sources.check 2>&1
rc=$?
cat $W/sources.check
[ "$rc" = 0 ] || { echo "FAIL: a staged QM9 file does not match qm9.sha256" | tee $W/check.txt; exit 1; }

{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
  python3 -u - "$TOOL" <<'PY' 2>&1
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
for m in (sys.argv[1], "numpy"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
  ls -l $P/lib/libblas.so* $P/lib/liblapack.so* 2>/dev/null | sed 's/.* \([^ ]* -> .*\)/\1/'
} > $W/host.txt
cat $W/host.txt

T0=$(date +%s)
if [ "$TOOL" = pyscf ]; then
  python3 -u /tmp/pyscf_run.py /tmp/qm9 $W "$NW" "$HA" "$CC" $MODE 2>&1 | tee $W/run.log
else
  python3 -u /tmp/psi4_run.py /tmp/qm9 $W "$NW" "$HA" "$CC" /tmp/in/pyscf.jsonl $MODE 2>&1 | tee $W/run.log
fi
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
