#!/usr/bin/env bash
# squidpy-real: host facts, then real.py on each section twice (r1, r2) in fresh processes, then the
# replicate comparison. Everything lands in /tmp/out/, which the spec stages out.
#
#   bash real.sh <n_jobs>
#
# Runs on the aarch.science `single-cell` image (arm64) and on its linux-64 env twin (x86), so the
# glue uses only /proc, /sys and coreutils.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl NUMBA_CACHE_DIR=/tmp/numba
NJ=$1
O=/tmp/out
mkdir -p "$O"
cd /tmp
# squidpy parallelises permutations over n_jobs worker processes; numba's own pool is the box
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=$NJ

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  python3 -c "
import ctypes, threadpoolctl
for p in threadpoolctl.threadpool_info():
    if p.get('internal_api') == 'openblas':
        lib = ctypes.CDLL(p['filepath']); lib.openblas_get_corename.restype = ctypes.c_char_p
        print('openblas', p.get('version'), lib.openblas_get_corename().decode())
" 2>&1
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

RC_ALL=0
: > "$O/times.txt"
for r in r1 r2; do
  for s in slideseq visium; do
    T0=$(date +%s)
    python3 -u real.py "$s" "/tmp/$s.h5ad" "$O/$r/$s" "$NJ" > "$O/$r-$s.out" 2>&1
    RC=$?
    T1=$(date +%s)
    echo "$r $s exit $RC wall_s $((T1 - T0))" | tee -a "$O/times.txt"
    grep -E '^(ok|FAIL|report) ' "$O/$r-$s.out" | cut -c1-240
    tail -n 3 "$O/$r-$s.out"
    if [ "$RC" != 0 ] || ! grep -q '^SQUIDPY REAL OK$' "$O/$r/$s/real.log"; then
      echo "FAIL: $r $s"; RC_ALL=1
    fi
  done
  [ "$RC_ALL" = 0 ] || break
done
for f in /sys/fs/cgroup/memory.peak; do
  [ -r "$f" ] && awk '{printf "cgroup_peak_gib %.2f\n", $1/2^30}' "$f" | tee -a "$O/times.txt"
done
[ "$RC_ALL" = 0 ] || exit 1

# the two runs must be byte-identical, every file but the logs and the timings
( cd "$O/r1" && find . -type f ! -name real.log ! -name phases.tsv | sort | xargs sha256sum ) > "$O/r1.sha256"
( cd "$O/r2" && sha256sum -c ../r1.sha256 ) > "$O/replicate.txt" 2>&1
RC=$?
cat "$O/replicate.txt"
[ "$RC" = 0 ] || { echo "FAIL: r1 and r2 differ"; exit 1; }
echo "replicate: $(wc -l < "$O/r1.sha256") files byte-identical across two fresh processes"
echo "SQUIDPY REAL OK"
