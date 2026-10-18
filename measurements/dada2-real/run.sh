#!/usr/bin/env bash
# dada2-real (the spec has already checked every staged byte against pins.sha256): record the box, run dada2-real.R under a footprint sampler.
#
#   bash run.sh <threads>
#
# Portable glue only (bioconda's x86 image is BusyBox: no date +%N, no sha256sum --quiet), and
# spawn runs task commands under bash -e (spawn#707), so errexit is cleared explicitly.
set +e
set -uo pipefail
NT="${1:?threads}"
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
OUT=/tmp/out; mkdir -p "$OUT"; cd /tmp || exit 1
{
  echo "arch $(uname -m)"; echo "nproc $(nproc)"
  awk -F: '/^model name/{sub(/^ /,"",$2); print "model", $2; exit}' /proc/cpuinfo
  sib=$(awk -F: '/^siblings/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo); cor=$(awk -F: '/^cpu cores/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo)
  # arm64 /proc/cpuinfo has neither field; read the kernel's topology instead, which both have
  tpc=$(awk -F, '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)
  case "$(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)" in *-*) tpc=2 ;; esac
  echo "threads_per_core ${tpc:-?} (cpuinfo siblings=${sib:-n/a} cores=${cor:-n/a})"
  command -v lscpu >/dev/null 2>&1 && lscpu | grep -E 'Model name|Thread\(s\) per core|Core\(s\) per socket|NUMA node\(s\)'
  awk '/MemTotal/{print "memtotal_kib", $2}' /proc/meminfo
} > "$OUT/box.txt" 2>&1
cat "$OUT/box.txt"
CG=/sys/fs/cgroup
( while : ; do printf '%s %s %s\n' "$(awk '{print $1}' /proc/uptime)" "$(awk '/^anon /{print $2; exit}' $CG/memory.stat 2>/dev/null)" "$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null)" >> "$OUT/samples.txt"; sleep 2; done ) &
SPID=$!
T0=$(awk '{print $1}' /proc/uptime)
Rscript dada2-real.R /tmp "$OUT" "$NT" > "$OUT/dada2-real.log" 2>&1
RC=$?
T1=$(awk '{print $1}' /proc/uptime)
kill "$SPID" 2>/dev/null
cat "$OUT/dada2-real.log"
{
  awk -v a="$T0" -v b="$T1" 'BEGIN{printf "tool_wall_s %.1f\n", b-a}'
  awk 'NR==1{t0=$1;u0=$3} $2>m{m=$2} {t=$1;u=$3} END{printf "peak_anon_gib %.2f\navg_busy_cores %.2f\n", m/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$OUT/samples.txt"
  echo "cgroup_memory_peak_bytes $(cat $CG/memory.peak 2>/dev/null || echo n/a)"
  echo "rscript_exit $RC"
} | tee "$OUT/footprint.txt"
test "$RC" = 0 || { echo "FAIL: dada2-real.R exited $RC"; exit 1; }
grep -q 'DADA2 REAL OK' "$OUT/dada2-real.log" || { echo "FAIL: no completion sentinel"; exit 1; }
test -s "$OUT/B-asv-totals.tsv" || { echo "FAIL: no ASV table"; exit 1; }
echo DONE
