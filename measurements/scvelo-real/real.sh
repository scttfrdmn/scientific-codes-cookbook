#!/usr/bin/env bash
# scvelo-real: host facts, then real.py on the whole atlas. Everything lands in /tmp/out/, which the
# spec stages out.
#
#   bash real.sh <n_jobs> [max_dynamical_genes]     # the cap is for a sizing probe only
#
# Runs on the aarch.science `single-cell` image (arm64) and on its linux-64 env twin (x86), so the
# glue uses only /proc, /sys and coreutils.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl NUMBA_CACHE_DIR=/tmp/numba
NJ=$1
MAXG=${2:-0}
O=/tmp/out
mkdir -p "$O"
cd /tmp
# n_jobs loky workers each fit genes serially: one BLAS/OpenMP thread per process; numba (kNN) gets the box
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=$NJ
# joblib memory-maps large arguments; Docker's /dev/shm is 64 MB, so point it at the tmpfs
export JOBLIB_TEMP_FOLDER=/tmp/joblib
mkdir -p "$JOBLIB_TEMP_FOLDER"

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  python3 -c "
import ctypes, threadpoolctl, numpy
for p in threadpoolctl.threadpool_info():
    if p.get('internal_api') == 'openblas':
        lib = ctypes.CDLL(p['filepath']); lib.openblas_get_corename.restype = ctypes.c_char_p
        print('openblas', p.get('version'), lib.openblas_get_corename().decode())
" 2>&1
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

T0=$(date +%s)
python3 -u real.py /tmp/gastrulation.h5ad "$O/r1" "$NJ" "$MAXG" > "$O/real.log" 2>&1
RC=$?
T1=$(date +%s)
echo "r1 exit $RC wall_s $((T1 - T0))" | tee "$O/times.txt"
grep -vE '^phase' "$O/real.log" | tail -n 60
grep -E '^phase' "$O/real.log"
for f in /sys/fs/cgroup/memory.peak; do
  [ -r "$f" ] && awk '{printf "cgroup_peak_gib %.2f\n", $1/2^30}' "$f" | tee -a "$O/times.txt"
done
if [ "$RC" != 0 ] || ! grep -q '^SCVELO REAL OK$' "$O/real.log"; then
  echo "FAIL: real.py"; exit 1
fi
echo "DONE"
