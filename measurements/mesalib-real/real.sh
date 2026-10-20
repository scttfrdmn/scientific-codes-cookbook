#!/usr/bin/env bash
# mesalib-real: the Western Alps SRTM mosaic through terrain.py, headless, under Xvfb.
#
#   bash real.sh <max threads> <frames> <tile.hgt.gz>...
#
# Everything lands in /tmp/out/ (staged out by the spec): host.txt, backends.tsv, prep.tsv,
# exact.tsv, exact.json, shade-t<N>.tsv/json, frame00.png, times.txt, xvfb.log and one log per stage.
set +e
set -uo pipefail
N=$1; F=$2; shift 2
O=/tmp/out
mkdir -p "$O" /tmp/work
cd /tmp
export PATH="${CONDA_PREFIX:-/opt/conda}/bin:$PATH"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
export XDG_CACHE_HOME=/tmp HOME=/tmp TMPDIR=/tmp LC_ALL=C OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  (lscpu 2>/dev/null | grep -iE 'model name|thread\(s\) per core|core\(s\) per socket|numa node\(s\)|flags') || echo "no lscpu"
  awk '/MemTotal/{print "mem_total_kib "$2}' /proc/meminfo
  df -h /tmp | tail -1
  echo "python $(command -v python3)"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

stage() {   # stage <name> <cmd...>: time it, keep its log, never let it kill the script
  local name=$1; shift
  local t0 t1 rc
  t0=$(date +%s)
  "$@" > "$O/$name.log" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "$name exit $rc wall_s $((t1-t0))" | tee -a "$O/times.txt"
  tail -4 "$O/$name.log"
  return $rc
}

# DIAGNOSTIC ONLY, own process: with no DISPLAY VTK segfaults rather than raising.
( unset DISPLAY; stage backends python3 -u terrain.py backends "$O" )

Xvfb :99 -screen 0 1024x768x24 > "$O/xvfb.log" 2>&1 &
XVFB=$!
for i in $(seq 1 200); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.05; done
export DISPLAY=:99

FAIL=0
stage prep python3 -u terrain.py prep "$O" "$@" || FAIL=1
if [ $FAIL = 0 ]; then
  LP_NUM_THREADS=$N stage exact python3 -u terrain.py exact "$O" || FAIL=1
fi
if [ $FAIL = 0 ]; then
  t=1
  while [ "$t" -le "$N" ]; do
    LP_NUM_THREADS=$t SAVE_FRAME_AT=$N stage "shade-t$t" python3 -u terrain.py shade "$O" "$t" "$F" || FAIL=1
    t=$((t*2))
  done
fi
kill "$XVFB" 2>/dev/null
{
  for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
    [ -r "$f" ] && echo "cgroup_peak_bytes $(cat "$f") $f"
  done
  df -h /tmp | tail -1
} | tee -a "$O/times.txt"
[ $FAIL = 0 ] || { echo "FAIL: a stage failed (see its log)"; exit 1; }
grep -q '^EXACT	OK' "$O/exact.tsv" || { echo "FAIL: no exact sentinel"; exit 1; }
echo "MESALIB REAL OK"
