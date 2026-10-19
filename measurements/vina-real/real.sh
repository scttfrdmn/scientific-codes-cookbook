#!/usr/bin/env bash
# vina-real: host facts, then dock.py under a wall clock, then the footprint.
#
#   bash real.sh <workers> [<subset.txt>]
#
# Runs on the aarch.science comp-chem image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
# one thread per docking process: Vina runs with cpu=1, and nothing else should start threads
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
W=$1 SUB=${2:-}
O=/tmp/out
mkdir -p "$O"
cd /tmp
tar -C /var/tmp -xf /tmp/prep.tar || { echo "FAIL: prep.tar does not extract"; exit 1; }

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  python3 -c "import vina, rdkit; print('vina', getattr(vina, '__version__', 'unknown'), 'rdkit', rdkit.__version__)"
  echo "openblas not used: Vina's scoring and search are its own C++ (Boost), no BLAS"
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
T0=$(date +%s)
# tee, so command.log carries the per-complex progress even if the task is killed
python3 -u dock.py /var/tmp/prep "$O" "$W" $SUB 2>&1 | tee "$O/dock.log" | grep -v '^$'
RC=${PIPESTATUS[0]}
T1=$(date +%s)
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  echo "python_exit $RC"
  echo "tool_wall_s $((T1-T0))"
  echo "cgroup_memory_peak_gib $(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)"
  awk -v u0="$U0" -v u1="$U1" -v t="$((T1-T0))" 'BEGIN{printf "avg_busy_cores %.2f\n", (t>0)?(u1-u0)/1e6/t:0}'
} | tee "$O/times.txt"
tar -C "$O" -czf "$O/poses.tgz" poses && rm -rf "$O/poses"
[ "$RC" = 0 ] || { echo "FAIL: dock.py exited $RC"; exit 1; }
grep -q '^VINA REAL OK$' "$O/dock.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/results.tsv" && test -s "$O/metrics.txt" || { echo "FAIL: an output is missing or empty"; exit 1; }
echo "VINA REAL OK"
