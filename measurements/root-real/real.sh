#!/usr/bin/env bash
# root-real: the whole CMS 2012 DoubleMuParked NanoAOD (61.5 M events) through ROOT's df102 dimuon
# analysis in one multi-threaded RDataFrame loop, then the same bytes through uproot.
#
#   bash real.sh <threads>
#
# Everything lands in /tmp/out/ (staged out by the spec): host.txt, root.tsv, check.tsv, the two
# histograms, analysis.log, check.log, times.txt.
set +e
set -uo pipefail
T=$1
F=/tmp/Run2012BC_DoubleMuParked_Muons.root
O=/tmp/out
mkdir -p "$O"
cd /tmp
export TMPDIR=/tmp HOME=/tmp DISPLAY=
# ROOT's IMT is the only parallelism; keep BLAS/OpenMP out of the way.
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
python3 -u analysis.py "$T" "$F" > "$O/analysis.log" 2>&1
RA=$?
T1=$(date +%s)
cat "$O/analysis.log"
echo "analysis exit $RA in $((T1-T0)) s" | tee "$O/times.txt"
test "$RA" = 0 && grep -q 'ROOT ANALYSIS OK' "$O/analysis.log" || { echo "FAIL: the ROOT analysis"; exit 1; }

python3 -u check.py "$T" "$F" > "$O/check.log" 2>&1
RC=$?
T2=$(date +%s)
cat "$O/check.log"
echo "check exit $RC in $((T2-T1)) s" | tee -a "$O/times.txt"
rm -f /tmp/os.root
test "$RC" = 0 && grep -q 'UPROOT CHECK OK' "$O/check.log" || { echo "FAIL: the uproot check"; exit 1; }
echo "REAL OK"
