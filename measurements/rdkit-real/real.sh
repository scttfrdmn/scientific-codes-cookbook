#!/usr/bin/env bash
# rdkit-real: host facts, a footprint sampler, then real.py under a wall clock.
#
#   bash real.sh <chemreps.txt.gz> <workers>
#
# Runs on the aarch.science comp-chem image (arm64) and on its linux-64 env twin (x86), so it uses
# only /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
# One thread per worker process: RDKit does not need BLAS here, and nothing should oversubscribe.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
SRC=$1 W=$2
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  echo "openblas not used: RDKit's InChI, formula and SMILES paths are integer/graph code"
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

# footprint every 5 s: anonymous memory (RSS, not page cache) and cumulative cgroup CPU
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$O/samples.txt"; sleep 5; done ) &
SPID=$!

T0=$(date +%s)
python3 -u real.py "$SRC" "$O" "$W" > "$O/real.log" 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null
{
  echo "python_exit $RC"
  echo "tool_wall_s $((T1-T0))"
  awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} {t=$1; u=$3} END{printf "peak_anon_gib %.2f\navg_busy_cores %.2f\n", m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples.txt"
} | tee "$O/times.txt"
tail -n 25 "$O/real.log"
# The 330 MB table is the cross-chip comparison object; compress it deterministically for staging.
gzip -n -6 "$O/rdkit_real.tsv"
[ "$RC" = 0 ] || { echo "FAIL: real.py exited $RC"; exit 1; }
grep -q '^RDKIT REAL OK$' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/rdkit_real.tsv.gz" && test -s "$O/metrics.txt" || { echo "FAIL: an output is missing or empty"; exit 1; }
echo "RDKIT REAL OK"
