#!/usr/bin/env bash
# measurements/cctbx-real: recompute deposited R-factors for one PDB hash directory with cctbx.
# Runs on the aarch.science structbio image (arm64) and its linux-64 env twin (x86), so it uses
# only /proc, /sys, coreutils and python. Everything kept lands in /tmp/out/.
set +e
set -uo pipefail
export HOME=/tmp TMPDIR=/tmp MPLCONFIGDIR=/tmp/mpl OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
O=/tmp/out
mkdir -p "$O/logs" /var/tmp/w
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "online_cpus	$(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores	$(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
  python3 -u -c "
import ctypes, glob, sys
libs = sorted(glob.glob(sys.prefix + '/lib/libopenblas*.so*'))
try:
    f = ctypes.CDLL(libs[0]).openblas_get_corename; f.restype = ctypes.c_char_p
    print('openblas_corename\t' + f().decode())
except Exception as e:
    print('openblas_corename\tunavailable (' + type(e).__name__ + ')')
" 2>&1
  df -h /tmp /var/tmp | sed 's/^/df	/'
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u /tmp/real.py 2>&1 | tee "$O/real.log"
RC=${PIPESTATUS[0]}
T1=$(date +%s)
echo "real_py_wall_s	$((T1 - T0))" >> "$O/host.txt"
echo "cgroup_memory_peak	$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$O/host.txt"
tar -czf "$O/logs.tar.gz" -C "$O" logs
[ "$RC" = 0 ] || { echo "FAIL: real.py exit $RC"; exit 1; }
grep -q 'CCTBX REAL OK' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
