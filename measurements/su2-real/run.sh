#!/usr/bin/env bash
# su2-real: the host facts, then real.py under a wall clock, then the footprint.
#
#   bash run.sh <ranks>
#
# Runs on the aarch.science `cfd-fv` image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
R=$1
O=/tmp/out
mkdir -p "$O" /tmp/work
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  echo "su2 $(command -v SU2_CFD)"
  echo "mpirun $(mpirun --version 2>&1 | head -n 1)"
  echo "su2_links_openblas $(ldd "$(command -v SU2_CFD)" 2>/dev/null | grep -c -i openblas)"
  python3 - <<'PY'
import ctypes, glob, os
p = sorted(glob.glob(os.path.join(os.environ.get("CONDA_PREFIX", "/opt/conda"), "lib", "libopenblas*.so*")))
if not p:
    import sys
    p = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
try:
    lib = ctypes.CDLL(p[0])
    lib.openblas_get_corename.restype = ctypes.c_char_p
    print("openblas_corename", lib.openblas_get_corename().decode())
except Exception as e:  # a description of the run must never fail it
    print("openblas_corename unknown (%s)" % type(e).__name__)
PY
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
T0=$(date +%s)
python3 -u real.py /tmp/data "$O" "$R" > "$O/real.out" 2>&1
RC=$?
T1=$(date +%s)
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  echo "real_py_exit $RC"
  echo "real_py_wall_s $((T1-T0))"
  # cgroup v2 high-water mark of the container's memory, not a periodic sample
  echo "cgroup_memory_peak_gib $(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)"
  awk -v a="$U0" -v b="$U1" -v t=$((T1-T0)) 'BEGIN{if (t>0 && b>a) printf "avg_busy_cores %.2f\n", (b-a)/1e6/t}'
} | tee "$O/times.txt"
tail -n 40 "$O/real.out"
[ "$RC" = 0 ] || { echo "FAIL: real.py exited $RC"; exit 1; }
grep -q 'SU2 REAL OK' "$O/real.out" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/check.txt" || { echo "FAIL: check.txt missing or empty"; exit 1; }
echo "SU2 REAL OK"
