#!/usr/bin/env bash
# slepc-real: host facts, then real.py under mpiexec for each grid size, timed, with the footprint.
#
#   bash real.sh "<N> [N ...]" <ranks> [nev_target]
#
# Runs on the aarch.science fem-cfd image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
export OMPI_MCA_btl_sm_backing_directory=/tmp     # Docker's /dev/shm is 64 MB (spawn#1111)
NS=$1 R=$2 NEVT=${3:-100}
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  echo "mpiexec $(mpiexec --version 2>&1 | head -n 1)"
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

RC_ALL=0
for N in $NS; do
  D="$O/N$N"; mkdir -p "$D"
  echo "== N=$N, $R ranks"
  U0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
  T0=$(date +%s)
  # one rank per vCPU on every chip: --use-hwthread-cpus makes an SMT thread a slot on x86
  mpiexec --use-hwthread-cpus --bind-to hwthread -n "$R" python3 -u real.py "$N" "$NEVT" "$R" "$D" > "$D/real.log" 2>&1
  RC=$?
  T1=$(date +%s)
  U1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
  {
    echo "real_py_exit $RC"
    echo "real_py_wall_s $((T1-T0))"
    awk -v a="$U0" -v b="$U1" -v t=$((T1-T0)) 'BEGIN{if (t>0 && b>a) printf "avg_busy_cores %.2f\n", (b-a)/1e6/t}'
  } | tee "$D/times.txt"
  grep -a -E 'CHECK|time_|^nev|^ks_|^alt_|checks_failed|rank_maxrss|corename' "$D/real.log" | tail -n 60
  if [ "$RC" != 0 ] || ! grep -a -q 'SLEPC REAL OK' "$D/real.log" || ! test -s "$D/eigenvalues.tsv"; then
    echo "FAIL: N=$N (exit $RC)"; tail -n 20 "$D/real.log"; RC_ALL=1
  fi
done
# cgroup v2 high-water mark of the container's memory across every rung, not a periodic sample
echo "cgroup_memory_peak_gib $(awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null)" | tee -a "$O/host.txt"
[ "$RC_ALL" = 0 ] || exit 1
echo "SLEPC REAL OK"
