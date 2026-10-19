#!/usr/bin/env bash
# measurements/qiskit-real: Qiskit over the whole QASMBench suite (real.py).
#   run.sh <worker processes>
set +e
set -uo pipefail
NP=${1:?workers}
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp
export HOME=/tmp
# one thread per worker process; the serial phases set Aer's own thread count explicitly
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 RAYON_NUM_THREADS=1
W=/tmp/out
mkdir -p $W
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
for m in ("qiskit", "qiskit_aer", "numpy", "rustworkx"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
} > $W/host.txt
cat $W/host.txt

echo "== the staged suite must match its pins =="
( cd /tmp/qasmbench && find . -type f ! -name qasmbench.sha256 | sed 's|^\./||' | LC_ALL=C sort | xargs -d '\n' sha256sum ) > $W/staged.sha256
LC_ALL=C sort -k2 /tmp/qasmbench.sha256 > /tmp/pinned.sha256
cmp -s /tmp/pinned.sha256 $W/staged.sha256 || { echo "FAIL: staged suite does not match qasmbench.sha256"; diff /tmp/pinned.sha256 $W/staged.sha256 | head; exit 1; }
echo "all $(wc -l < /tmp/pinned.sha256) files match qasmbench.sha256"

T0=$(date +%s)
python3 -u /tmp/real.py /tmp/qasmbench $W "$NP" 2>&1 | grep -v 'DeprecationWarning' | tee $W/real.log
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
