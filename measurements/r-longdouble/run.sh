#!/usr/bin/env bash
# r-longdouble: host facts, then probe.R under a wall clock. Everything lands in /tmp/out/.
set +e
export PATH=/opt/conda/bin:/usr/local/bin:$PATH TMPDIR=/tmp HOME=/tmp
NT=$1; O=/tmp/out; mkdir -p "$O"; cd /tmp
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "openblas $(OPENBLAS_VERBOSE=2 Rscript -e 'invisible(crossprod(matrix(1, 300, 300)))' 2>&1 | grep -i 'core' | head -n 1)"
} > "$O/host.txt" 2>&1
cat "$O/host.txt"
export OPENBLAS_NUM_THREADS=$NT OMP_NUM_THREADS=$NT
T0=$(date +%s)
Rscript probe.R "$O" "$NT" > "$O/probe.out" 2>&1
RC=$?
echo "rscript_exit $RC wall_s $(( $(date +%s) - T0 ))" | tee -a "$O/host.txt"
cat "$O/probe.out"
grep -q '^PROBE  *OK' "$O/results.tsv" || { echo "FAIL: probe did not complete"; exit 1; }
