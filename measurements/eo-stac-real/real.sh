#!/usr/bin/env bash
# eo-stac-real: California's 2024 Sentinel-2 L2A through pystac-client, held to the bucket (real.py).
#
#   bash real.sh <pin|check> <threads>
#
# Everything lands in /tmp/out/ (staged out by the spec): host.txt, real.log, times.txt, score.tsv,
# checks.tsv, tiles.tsv, dangling.tsv, and in pin mode snapshot/.
set +e
set -uo pipefail
MODE=$1
NT=$2
O=/tmp/out
mkdir -p "$O"
cd /tmp
export TMPDIR=/tmp HOME=/tmp LC_ALL=C OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  (lscpu 2>/dev/null | grep -iE 'model name|thread\(s\) per core|core\(s\) per socket|numa node\(s\)') || echo "no lscpu"
  awk '/MemTotal/{print "mem_total_kib "$2}' /proc/meminfo
  echo "python $(command -v python3)"
  python3 -c 'import ctypes, glob, os, sys
p = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(sys.executable))), "lib", "libopenblas*.so*")))
f = ctypes.CDLL(p[0]).openblas_get_corename if p else None
if f: f.restype = ctypes.c_char_p
print("openblas_corename", f().decode() if f else "none found")' 2>&1 || echo "openblas_corename unknown"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u real.py "$MODE" /tmp "$O" "$NT" > "$O/real.log" 2>&1
RC=$?
T1=$(date +%s)
cat "$O/real.log"
echo "real.py exit $RC in $((T1-T0)) s" | tee "$O/times.txt"
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r "$f" ] && echo "cgroup_memory_peak_bytes $(cat "$f") ($f)" | tee -a "$O/times.txt"
done
test "$RC" = 0 && grep -q 'REAL OK' "$O/real.log" || { echo "FAIL: real.py"; exit 1; }
echo "REAL OK"
