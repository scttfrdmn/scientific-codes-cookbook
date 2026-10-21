#!/usr/bin/env bash
# yoda-real: fill YODA objects from all 61.5 M events of CMS record 12341 (fill.py), then read and
# round-trip every reference file Rivet 4.1.4 ships (refs.py).
#
#   bash real.sh <workers>
#
# Everything lands in /tmp/out/ (staged out by the spec): host.txt, fill.tsv, refs.tsv, fill.log,
# refs.log, cms.yoda.gz, yodamerge-help.txt, times.txt.
set +e
set -uo pipefail
W=$1
O=/tmp/out
mkdir -p "$O"
cd /tmp
export TMPDIR=/tmp HOME=/tmp PYTHONIOENCODING=utf-8
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
python3 -u fill.py "$W" /tmp/Run2012BC_DoubleMuParked_Muons.root /tmp/hist-mass.txt /tmp/hist-nmuon.txt "$O" 2>&1 | tee "$O/fill.log"
RF=${PIPESTATUS[0]}
T1=$(date +%s)
echo "fill exit $RF in $((T1-T0)) s" | tee "$O/times.txt"
python3 -u refs.py "$W" /tmp/rivet-4.1.4-analyses.tar.gz /tmp/refs.manifest "$O" 2>&1 | tee "$O/refs.log"
RR=${PIPESTATUS[0]}
T2=$(date +%s)
echo "refs exit $RR in $((T2-T1)) s" | tee -a "$O/times.txt"
for f in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes; do
  [ -r "$f" ] && echo "cgroup_memory_peak_bytes $(cat "$f") ($f)" | tee -a "$O/times.txt"
done
df -h /tmp | tail -1 | tee -a "$O/times.txt"
test "$RF" = 0 && grep -q 'FILL OK' "$O/fill.log" || { echo "FAIL: fill.py"; exit 1; }
test "$RR" = 0 && grep -q 'REFS OK' "$O/refs.log" || { echo "FAIL: refs.py"; exit 1; }
echo "REAL OK"
