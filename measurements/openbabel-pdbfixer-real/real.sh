#!/usr/bin/env bash
# openbabel-pdbfixer-real: host facts, then one workload under a wall clock.
#
#   bash real.sh ob  <chemreps.txt.gz> <rdkit_real.tsv.gz> <workers> [limit]
#   bash real.sh fix <mmCIF dir> <workers> [ids file]
#
# Runs on the aarch.science comp-chem image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys, coreutils and python. Everything kept lands in $OUTDIR (/tmp/out/), which the spec
# stages out; the tool's log is also teed to the task log, which spawn keeps even if the box is killed.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
# One thread per worker process: neither tool needs BLAS here, and nothing should oversubscribe.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OPENMM_CPU_THREADS=1
MODE=$1; shift
O=${OUTDIR:-/tmp/out}
mkdir -p "$O"
cd /tmp
H="$O/host-$MODE.txt"
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)):'
  grep -m1 -E '^(model name|CPU part)' /proc/cpuinfo
  grep MemTotal /proc/meminfo
  echo "openblas not used: Open Babel is integer/graph code; PDBFixer's minimiser runs on OpenMM's CPU platform"
} > "$H" 2>&1
cat "$H"

T0=$(date +%s)
case $MODE in
  ob)  python3 -u /tmp/ob_real.py "$1" "$2" "$O" "$3" "${4:-0}" 2>&1 | tee "$O/ob_real.log"; RC=${PIPESTATUS[0]}; LOG=$O/ob_real.log; OK='OPENBABEL REAL OK' ;;
  fix) mkdir -p /var/tmp/fix
       WORKDIR=/var/tmp/fix python3 -u /tmp/fix_real.py "$1" "$O" "$2" "${3:-}" 2>&1 | tee "$O/fix_real.log"; RC=${PIPESTATUS[0]}; LOG=$O/fix_real.log; OK='PDBFIXER REAL OK' ;;
  *) echo "FAIL: unknown mode $MODE"; exit 1 ;;
esac
T1=$(date +%s)
{
  echo "python_exit $RC"
  echo "tool_wall_s $((T1-T0))"
  echo "cgroup_memory_peak_bytes $(cat /sys/fs/cgroup/memory.peak 2>/dev/null)"
} | tee -a "$H"
[ -f "$O/ob_real.tsv" ] && gzip -n -6 -f "$O/ob_real.tsv"
[ -f "$O/entries.tsv" ] && cp "$O/entries.tsv" "$O/entries-$MODE.tsv"
[ "$RC" = 0 ] || { echo "FAIL: $MODE exited $RC"; exit 1; }
grep -q "^$OK\$" "$LOG" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/metrics.txt" || { echo "FAIL: no metrics"; exit 1; }
echo "$OK"
