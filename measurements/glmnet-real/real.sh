#!/usr/bin/env bash
# glmnet-real: the host facts, a footprint sampler, then real.R under a wall clock.
#
#   bash real.sh <data dir> <expected genes> <expected samples>
#
# Runs on the aarch.science `r` image (arm64) and on its linux-64 env twin (x86), so it uses only
# /proc, /sys and coreutils. Everything lands in /tmp/out/, which the spec stages out.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
DATA=$1 WG=$2 WS=$3
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  # conda's OpenBLAS is DYNAMIC_ARCH: the kernel it picks on this host, from its own verbose line
  echo "openblas $(OPENBLAS_VERBOSE=2 Rscript -e 'invisible(crossprod(matrix(1, 300, 300)))' 2>&1 | grep -i 'core' | head -n 1)"
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

# footprint every 5 s: anonymous memory (RSS, not page cache) and cumulative cgroup CPU
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$O/samples.txt"; sleep 5; done ) &
SPID=$!

T0=$(date +%s)
Rscript real.R "$DATA" "$O" "$WG" "$WS" > "$O/rscript.out" 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null
{
  echo "rscript_exit $RC"
  echo "rscript_wall_s $((T1-T0))"
  awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} {t=$1; u=$3} END{printf "peak_anon_gib %.2f\navg_busy_cores %.2f\n", m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples.txt"
} | tee "$O/times.txt"
tail -n 5 "$O/rscript.out"
[ "$RC" = 0 ] || { echo "FAIL: real.R exited $RC"; exit 1; }
grep -q '^GLMNET REAL OK$' "$O/real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/score.tsv" && test -s "$O/A-path.tsv" && test -s "$O/B-path.tsv" || { echo "FAIL: an output is missing or empty"; exit 1; }
echo "GLMNET REAL OK"
