#!/usr/bin/env bash
# paraview-real: a week of hourly ERA5 3D temperature through week.py under pvbatch, headless.
#
#   bash real.sh <threads> <file.nc> [file.nc ...]
#
# Everything lands in /tmp/out/ (staged out by the spec): host.txt, meta.json, hours.tsv,
# smoke-check.txt, summary.tsv, pvbatch.log, times.txt and frames/.
set +e
set -uo pipefail
N=$1; shift
O=/tmp/out
mkdir -p "$O"
cd /tmp
export PATH="${CONDA_PREFIX:-/opt/conda}/bin:$PATH"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe LP_NUM_THREADS="$N"
export XDG_CACHE_HOME=/tmp HOME=/tmp TMPDIR=/tmp LC_ALL=C
# VTK's SMP pool is the parallelism; numpy and BLAS stay on one thread.
export VTK_SMP_MAX_THREADS="$N" PV_SMP_BACKEND="${PV_SMP_BACKEND:-TBB}" OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  (lscpu 2>/dev/null | grep -iE 'model name|thread\(s\) per core|core\(s\) per socket|numa node\(s\)') || echo "no lscpu"
  awk '/MemTotal/{print "mem_total_kib "$2}' /proc/meminfo
  df -h /tmp | tail -1
  echo "pvbatch $(command -v pvbatch)"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

Xvfb :99 -screen 0 1024x768x24 >/dev/null 2>&1 &
XVFB=$!
for i in $(seq 1 200); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.05; done
export DISPLAY=:99

T0=$(date +%s)
pvbatch week.py "$O" "$@" > "$O/pvbatch.log" 2>&1
RC=$?
T1=$(date +%s)
kill "$XVFB" 2>/dev/null
tail -30 "$O/pvbatch.log"
{
  echo "pvbatch_exit $RC"
  echo "pvbatch_wall_s $((T1-T0))"
  for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
    [ -r "$f" ] && echo "cgroup_peak_bytes $(cat "$f") $f"
  done
  df -h /tmp | tail -1
} | tee "$O/times.txt"
grep -q 'PARAVIEW REAL OK' "$O/pvbatch.log" || { echo "FAIL: no completion sentinel"; exit 1; }
echo "REAL OK"
