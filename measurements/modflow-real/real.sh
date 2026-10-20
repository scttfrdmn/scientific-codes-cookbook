#!/usr/bin/env bash
# measurements/modflow-real: the USGS Mobile Bay MODFLOW 6 model, both published simulations.
# Runs on the aarch.science geoscience image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys, coreutils and python. Everything kept lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export HOME=/tmp TMPDIR=/tmp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
O=/tmp/out
mkdir -p "$O" /var/tmp/w
cd /tmp
{
  echo "uname_m	$(uname -m)"
  echo "online_cpus	$(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores	$(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)):' | sed 's/:  */	/'
  grep MemTotal /proc/meminfo
  echo "mf6_v	$(mf6 -v 2>&1 | tr '\n' ' ')"
  python3 -u -c "import flopy, numpy; print('flopy\t' + str(getattr(flopy, '__version__', 'unknown'))); print('numpy\t' + str(getattr(numpy, '__version__', 'unknown')))" 2>&1
  df -h /tmp /var/tmp | sed 's/^/df	/'
} > "$O/host.txt" 2>&1
cat "$O/host.txt"

T0=$(date +%s)
python3 -u /tmp/real.py 2>&1 | tee "$O/real.log"
RC=${PIPESTATUS[0]}
T1=$(date +%s)
echo "real_py_wall_s	$((T1 - T0))" >> "$O/host.txt"
echo "cgroup_memory_peak	$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$O/host.txt"
echo "var_tmp_used	$(du -sh /var/tmp/w 2>/dev/null | cut -f1)" >> "$O/host.txt"
[ "$RC" = 0 ] || { echo "FAIL: real.py exit $RC"; exit 1; }
grep -q 'MODFLOW REAL OK' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
echo DONE
