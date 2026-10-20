#!/usr/bin/env bash
# fastjet-real: anti-k_t R = 1.0 over all 1.1 M LHCO 2020 R&D events, with the identities in cluster.py.
#
#   bash real.sh <workers>
#
# Everything lands in /tmp/out/ (staged out by the spec): host.txt, score.tsv, cluster.log,
# lead.npy, taus.npy, features_to_event.npy, times.txt.
set +e
set -uo pipefail
W=$1
O=/tmp/out
mkdir -p "$O"
cd /tmp
export TMPDIR=/tmp HOME=/tmp
# the worker processes are the only parallelism; keep BLAS/OpenMP to one thread each
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  (lscpu 2>/dev/null | grep -iE 'model name|thread\(s\) per core|core\(s\) per socket|numa node\(s\)') || echo "no lscpu"
  awk '/MemTotal/{print "mem_total_kib "$2}' /proc/meminfo
  df -h /tmp | tail -1
  echo "python $(command -v python3)"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u cluster.py /tmp "$W" "$O" > "$O/cluster.log" 2>&1
RC=$?
T1=$(date +%s)
cat "$O/cluster.log"
echo "cluster exit $RC in $((T1-T0)) s" | tee "$O/times.txt"
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r "$f" ] && echo "cgroup_memory_peak_bytes $(cat "$f") ($f)" | tee -a "$O/times.txt"
done
df -h /tmp | tail -1 | tee -a "$O/times.txt"
test "$RC" = 0 && grep -q 'CLUSTER OK' "$O/cluster.log" || { echo "FAIL: cluster.py"; exit 1; }
echo "REAL OK"
