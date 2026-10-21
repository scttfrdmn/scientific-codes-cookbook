#!/usr/bin/env bash
# tomography-real: host facts, then real.py on the whole BBii scan. Everything lands in /tmp/out/,
# which the spec stages out.
#
#   bash real.sh <ncore> [max_rows] [cgls_iterations]     # max_rows > 0 is a sizing probe only
#
# Runs on the aarch.science `tomography` image (arm64) and on its linux-64 env twin (x86), so the
# glue uses only /proc, /sys and coreutils.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl
NC=$1
MAXR=${2:-0}
export CGLS_IT=${3:-30}
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  df -h /tmp | tail -n 1
  python3 -c "
import ctypes, glob, os, sys
lib = os.path.join(sys.prefix, 'lib')
print('env_prefix', sys.prefix)
for p in sorted(glob.glob(os.path.join(lib, 'libopenblas*.so*')))[:1]:
    b = ctypes.CDLL(p); b.openblas_get_corename.restype = ctypes.c_char_p
    print('openblas_corename', b.openblas_get_corename().decode(), os.path.basename(p))
print('libs', ' '.join(sorted(os.path.basename(x) for x in glob.glob(os.path.join(lib, '*.so'))
      if any(k in x for k in ('libtomo', 'fftw', 'astra', 'mkl')))))
" 2>&1
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

T0=$(date +%s)
python3 -u real.py /tmp/proj.zip /tmp/rec.zip "$O/r1" "$NC" "$MAXR" > "$O/real.log" 2>&1
RC=$?
T1=$(date +%s)
echo "r1 exit $RC wall_s $((T1 - T0))" | tee "$O/times.txt"
grep -vE '^phase' "$O/real.log" | tail -n 120
grep -E '^phase' "$O/real.log"
for f in /sys/fs/cgroup/memory.peak; do
  [ -r "$f" ] && awk '{printf "cgroup_peak_gib %.2f\n", $1/2^30}' "$f" | tee -a "$O/times.txt"
done
df -h /tmp | tail -n 1 | tee -a "$O/times.txt"
if [ "$RC" != 0 ] || ! grep -q '^TOMOGRAPHY REAL OK$' "$O/real.log"; then
  echo "FAIL: real.py"; exit 1
fi
echo "DONE"
