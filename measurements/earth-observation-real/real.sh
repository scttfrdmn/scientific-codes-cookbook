#!/usr/bin/env bash
# earth-observation-real: the whole 2024 year of Sentinel-2 tile 11SKA through rasterio (real.py).
#
#   bash real.sh <workers> [stride]
#
# Everything lands in /tmp/out/ (staged out by the spec): host.txt, real.log, times.txt,
# score.tsv, scenes.tsv, ndvi-max-2024.tif, ndvi-count-2024.tif.
set +e
set -uo pipefail
W=$1
STRIDE=${2:-1}
O=/tmp/out
mkdir -p "$O"
cd /tmp
export TMPDIR=/tmp HOME=/tmp LC_ALL=C
# the worker processes are the only parallelism: one GDAL / BLAS / OpenMP thread each
export GDAL_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 GDAL_CACHEMAX=256

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  (lscpu 2>/dev/null | grep -iE 'model name|thread\(s\) per core|core\(s\) per socket|numa node\(s\)') || echo "no lscpu"
  awk '/MemTotal/{print "mem_total_kib "$2}' /proc/meminfo
  df -h /tmp /var/tmp | tail -2
  echo "python $(command -v python3)"
  python3 -c 'import ctypes, glob, os
p = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__import__("sys").executable))), "lib", "libopenblas*.so*")))
f = ctypes.CDLL(p[0]).openblas_get_corename if p else None
if f: f.restype = ctypes.c_char_p
print("openblas_corename", f().decode() if f else "none found")' 2>&1 || echo "openblas_corename unknown"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u real.py /tmp "$W" "$O" "$STRIDE" > "$O/real.log" 2>&1
RC=$?
T1=$(date +%s)
cat "$O/real.log"
echo "real.py exit $RC in $((T1-T0)) s" | tee "$O/times.txt"
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r "$f" ] && echo "cgroup_memory_peak_bytes $(cat "$f") ($f)" | tee -a "$O/times.txt"
done
df -h /tmp /var/tmp | tail -2 | tee -a "$O/times.txt"
test "$RC" = 0 && grep -q 'REAL OK' "$O/real.log" || { echo "FAIL: real.py"; exit 1; }
echo "REAL OK"
