#!/usr/bin/env bash
# z3-real: host facts, a footprint sampler, then the driver and the checks.
#
#   bash real.sh <data dir> <workers> <time limit s> <memory MiB per solve> [<per-logic limit>]
#
# Runs on the aarch.science `optimization` image (arm64) and on its linux-64 env twin (x86), so it
# uses only /proc, /sys, coreutils and the env's python. Everything lands in /tmp/out/.
set +e
set -uo pipefail
export TMPDIR=/tmp HOME=/tmp
DATA=$1 W=$2 TL=$3 MEM=$4 NLIM=${5:-0}
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  grep MemTotal /proc/meminfo
  df -h /tmp | tail -n 1
  python3 -I -c 'import z3, sys; print("z3", z3.get_version_string(), "python", sys.version.split()[0])'
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

# footprint every 5 s: anonymous memory, cumulative cgroup CPU, tmpfs use. Per-solve peaks come
# from ru_maxrss in the driver, not from this sampler.
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null) $(df -k /tmp | awk 'NR==2{print $3}')" >> "$O/samples.txt"; sleep 5; done ) &
SPID=$!

T0=$(date +%s)
python3 -u -I /tmp/driver.py "$DATA" "$O" "$W" "$TL" "$MEM" "$NLIM" > "$O/driver.log" 2>&1
RC=$?
T1=$(date +%s)
kill $SPID 2>/dev/null
tail -n 8 "$O/driver.log"
EXPECT="QF_IDL=2528,QF_RDL=255,QF_LRA=1753"
[ "$NLIM" = 0 ] || EXPECT=""
python3 -u -I /tmp/summarize.py "$O" "$EXPECT" > "$O/summary.log" 2>&1
SRC=$?
{
  echo "driver_exit $RC"
  echo "summarize_exit $SRC"
  echo "driver_wall_s $((T1-T0))"
  awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} $4>d{d=$4} {t=$1; u=$3} END{printf "peak_anon_gib %.2f\npeak_tmp_gib %.2f\navg_busy_cores %.2f\n", m/1073741824, d/1048576, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples.txt"
} | tee "$O/times.txt"
tail -n 60 "$O/summary.log"
( cd "$O" && tar czf logs.tgz logs rec && rm -rf logs rec )
[ "$RC" = 0 ] || { echo "FAIL: driver exited $RC"; exit 1; }
[ "$SRC" = 0 ] || { echo "FAIL: a check failed (summary.log)"; exit 1; }
grep -q '^Z3 REAL OK$' "$O/summary.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$O/results.jsonl" && test -s "$O/score.tsv" || { echo "FAIL: an output is missing or empty"; exit 1; }
echo "Z3 REAL OK"
