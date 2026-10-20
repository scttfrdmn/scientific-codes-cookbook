#!/usr/bin/env bash
# arviz-real: the host facts, then arviz_real.py under a wall clock, then the footprint.
#
#   bash real.sh <pdb dir> <workers>
#
# Runs on the aarch.science `bayes` image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
PDB=$1 W=$2
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  command -v lscpu >/dev/null 2>&1 && lscpu | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket)'
  echo "gxx $(g++ --version 2>&1 | head -n 1)"
  python3 -I - <<'PY'
import ctypes, glob, os, sys
libs = sorted(glob.glob(os.path.join(sys.prefix, "lib", "libopenblas*.so*")))
name = "no libopenblas in " + sys.prefix
for p in libs:
    try:
        f = ctypes.CDLL(p).openblas_get_corename
        f.restype = ctypes.c_char_p
        name = "%s (%s)" % (f().decode(), os.path.basename(p))
        break
    except Exception as e:  # noqa: BLE001 -- describing the host must not fail the run
        name = "unavailable: " + str(e)[:80]
print("openblas_corename " + name)
PY
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
T0=$(date +%s)
python3 -u arviz_real.py "$PDB" "$O" "$W" > "$O/arviz_real.out" 2>&1
RC=$?
T1=$(date +%s)
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  echo "arviz_real_exit $RC"
  echo "arviz_real_wall_s $((T1-T0))"
  # cgroup v2 high-water mark of the container's memory, not a periodic sample
  echo "cgroup_memory_peak_gib $(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)"
  awk -v a="$U0" -v b="$U1" -v t=$((T1-T0)) 'BEGIN{if (t>0 && b>a) printf "avg_busy_cores %.2f\n", (b-a)/1e6/t}'
} | tee "$O/times.txt"
tail -n 40 "$O/arviz_real.out"
[ "$RC" = 0 ] || { echo "FAIL: arviz_real.py exited $RC"; exit 1; }
grep -q 'ARVIZ REAL OK' "$O/arviz_real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
for f in reference_diag.tsv fit_vs_stansummary.tsv draws.tsv; do
  test -s "$O/$f" || { echo "FAIL: $f missing or empty"; exit 1; }
done
echo "ARVIZ REAL OK"
