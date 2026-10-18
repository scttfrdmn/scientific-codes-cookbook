#!/usr/bin/env bash
# methylation-array-real task wrapper (both images): check every staged IDAT against its pin, record
# the host, sample the footprint, run one R script, and leave its log in /tmp/out/ whatever happens.
#
#   bash run.sh <script.R>
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp LC_ALL=C
[ -n "${HOME:-}" ] && [ -w "$HOME" ] || export HOME=/tmp
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "threads_per_core $(( $(grep -c '^processor' /proc/cpuinfo) / $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l) ))"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  awk '/^MemTotal/{printf "mem_total_gib %.1f\n", $2/1048576}' /proc/meminfo
  df -h /tmp | tail -1 | awk '{print "tmp_fs " $2 " total"}'
  echo "R $(command -v R)"
} > "$O/host.txt"
cat "$O/host.txt"

T0=$(date +%s)
( cd /tmp/idat && sha256sum -c /tmp/idat.sha256 > /tmp/idat-check.txt 2>&1 ); RC=$?
OK=$(grep -c ': OK$' /tmp/idat-check.txt)
echo "idat_pins $OK/1378 OK, exit $RC in $(( $(date +%s) - T0 )) s" | tee "$O/pins.txt"
[ "$RC" = 0 ] && [ "$OK" = 1378 ] || { echo "FAIL: a staged IDAT does not match its pin"; exit 1; }

# footprint every 5 s: anonymous memory (RSS, not page cache or tmpfs) and cumulative cgroup CPU
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null) $(df -k /tmp | awk 'NR==2{print $3}')" >> "$O/samples.txt"; sleep 5; done ) &
SPID=$!
T1=$(date +%s)
Rscript "$1" > "$O/r.log" 2>&1; RC=$?
T2=$(date +%s)
kill $SPID 2>/dev/null
cat "$O/r.log"
{
  echo "tool_wall_s $((T2-T1))"
  awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} $4>k{k=$4} {t=$1; u=$3} END{printf "peak_anon_gib %.2f\navg_busy_cores %.2f\npeak_tmp_gib %.2f\n", m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0, k/1048576}' "$O/samples.txt"
} | tee "$O/footprint.txt"
echo "Rscript exit $RC"
exit $RC
