#!/usr/bin/env bash
# measurements/popgen-real: scikit-allel over 1000 Genomes Phase 3 and msprime over the same
# genome, one process per chromosome per workload (real.py).
#   run.sh <worker processes> <chromosomes...>
set +e
set -uo pipefail
NP=${1:?workers}; shift
CHROMS="$*"
export TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl
# one process per chromosome, so one BLAS/OpenMP thread per process (numpy links OpenBLAS)
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
W=/tmp/out
mkdir -p $W
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "nproc_all	$(nproc --all)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  echo "model	$(grep -m1 -E '^(model name|Model name)' /proc/cpuinfo | sed 's/.*: //')"
  echo "cpu_part	$(grep -m1 'CPU part' /proc/cpuinfo | sed 's/.*: //')"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU\(s\)|NUMA node\(s\)):' | sed 's/:  */	/'
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
print("python\t%s" % sys.version.split()[0])
for m in ("msprime", "tskit", "allel", "numpy", "scipy"):
    try:
        print("%s_version\t%s" % (m, str(getattr(__import__(m), "__version__", "unknown"))))
    except Exception as e:
        print("%s_version\tunimportable: %s" % (m, e))
PY
} > $W/host.txt
cat $W/host.txt

echo "== staged inputs must match their pins =="
T0=$(date +%s)
for c in $CHROMS; do grep -F ".chr$c.phase3" /tmp/pins.sha256; done > /tmp/pins.sel
grep -E 'panel|id-all' /tmp/pins.sha256 >> /tmp/pins.sel
# hash in parallel; each line checked on its own so a mismatch names its file
xargs -P "$NP" -L 1 sh -c 'echo "$0  $1" | sha256sum -c -' < /tmp/pins.sel > $W/pins.check 2>&1
NOK=$(grep -c ': OK$' $W/pins.check)
NWANT=$(wc -l < /tmp/pins.sel | tr -d ' ')
echo "$NOK of $NWANT inputs match their pins in $(( $(date +%s) - T0 ))s"
[ "$NOK" = "$NWANT" ] || { cat $W/pins.check; echo "FAIL: a staged input does not match its pin"; exit 1; }

T1=$(date +%s)
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
python3 -u /tmp/real.py /tmp $W "$NP" $CHROMS 2>&1 | tee $W/real.log
CHK=${PIPESTATUS[0]}
T2=$(date +%s)
U2=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  echo "pin_check_s	$((T1-T0))"
  echo "tool_wall_s	$((T2-T1))"
  CG=/sys/fs/cgroup
  [ -r $CG/memory.peak ] && awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' $CG/memory.peak
  [ -n "$U1" ] && [ -n "$U2" ] && awk -v a="$U1" -v b="$U2" -v w=$((T2-T1)) 'BEGIN{printf "avg_busy_cores_tool\t%.2f\n", (b-a)/(w*1e6)}'
  df -h /tmp /var/tmp | tail -2
} | tee $W/footprint.txt
[ "$CHK" = 0 ] && grep -q 'POPGEN REAL OK' $W/real.log && echo "ALL CHECKS PASSED" || { echo "FAIL: check rc=$CHK"; exit 1; }
