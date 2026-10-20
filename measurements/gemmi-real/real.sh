#!/usr/bin/env bash
# measurements/gemmi-real: gemmi over the whole wwPDB archive, RODA snapshot 20260101.
# Runs on the aarch.science md image (arm64) and on its linux-64 env twin (x86), so it uses only
# /proc, /sys, coreutils and python. Everything kept lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export HOME=/tmp TMPDIR=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
O=/tmp/out
mkdir -p "$O"
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "online_cpus	$(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores	$(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  echo "threads_per_core_cpu0	$(tr ',' '\n' < /sys/devices/system/cpu/cpu0/topology/thread_siblings_list | wc -l)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)):' | sed 's/:  */	/'
  grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo
  grep MemTotal /proc/meminfo
  python3 -u -c "import gemmi, numpy; print('gemmi\t' + str(getattr(gemmi, '__version__', 'unknown'))); print('numpy\t' + str(getattr(numpy, '__version__', 'unknown')))" 2>&1
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u /tmp/real.py 2>&1 | tee "$O/real.log"
RC=${PIPESTATUS[0]}
T1=$(date +%s)
echo "real_py_wall_s	$((T1 - T0))" >> "$O/host.txt"
echo "cgroup_memory_peak	$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$O/host.txt"
gzip -n -f "$O/entries.tsv" 2>/dev/null
cat "$O/host.txt" | tail -2
[ "$RC" = 0 ] || { echo "FAIL: real.py exit $RC"; exit 1; }
grep -q 'GEMMI REAL OK' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/entries.tsv.gz" || { echo "FAIL: no per-entry table"; exit 1; }
echo DONE
