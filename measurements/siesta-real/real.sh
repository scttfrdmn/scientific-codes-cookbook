#!/usr/bin/env bash
# siesta-real: the host facts, then real.py under a wall clock, then the footprint.
#
#   bash real.sh <suite dir> <ranks per test> <tests at once> <qdot ranks>
#
# Runs on the aarch.science `dft` image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys and coreutils. Results land in /tmp/out/, which the spec stages out; the work
# tree is on the root disk (/var/tmp), not the half-RAM /tmp tmpfs.
set +e
set -uo pipefail
command -v siesta >/dev/null || export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export OMPI_MCA_btl_sm_backing_directory=/tmp
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
SUITE=$1 NP=$2 CONC=$3 QNP=$4
O=/tmp/out W=/var/tmp/siesta-real
mkdir -p "$O" "$W"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  df -h /var/tmp /tmp | tail -n 2
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
T0=$(date +%s)
python3 -u real.py "$SUITE" "$W" "$O" "$NP" "$CONC" "$QNP" > "$O/real.out" 2>&1
RC=$?
T1=$(date +%s)
U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
{
  echo "real_py_exit $RC"
  echo "real_py_wall_s $((T1-T0))"
  # cgroup v2 high-water mark of the container's memory, not a periodic sample
  echo "cgroup_memory_peak_gib $(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)"
  awk -v a="$U0" -v b="$U1" -v t=$((T1-T0)) 'BEGIN{if (t>0 && b>a) printf "avg_busy_cores %.2f\n", (b-a)/1e6/t}'
  du -sh "$W" 2>/dev/null | awk '{print "work_tree " $1}'
} | tee "$O/times.txt"
# every SIESTA output and the suite's own verify report, for the record (not the binary files)
( cd "$W" && find . -name '*.out' -o -name 'verify.txt' -o -name '*.yml' -o -name 'stdout.txt' -o -name '*.stdout' -o -name '*.fdf' \
    | tar -czf "$O/outputs.tar.gz" -T - ) 2>/dev/null
tail -n 40 "$O/real.out"
[ "$RC" = 0 ] || { echo "FAIL: real.py exited $RC"; exit 1; }
grep -q 'SIESTA REAL OK' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
for f in suite.tsv digest.tsv qdot.tsv; do test -s "$O/$f" || { echo "FAIL: $f missing or empty"; exit 1; }; done
echo "SIESTA REAL OK"
