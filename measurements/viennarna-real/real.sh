#!/usr/bin/env bash
# viennarna-real: fold every seed sequence of every Rfam 15.1 family (109,390 RNAs, 19-4,065 nt)
# with RNAfold -p -- MFE, partition function, centroid -- twice at -j<threads>, and check every
# record (real.py).
#
#   bash real.sh <threads>
#
# Everything lands in /tmp/out/. Runs on the aarch.bio image and on bioconda's x86 one.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 TMPDIR=/tmp HOME=/tmp
T=$1
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "RNAfold $(command -v RNAfold) $(RNAfold --version 2>&1)"
  echo "RNAfold links: $(ldd "$(command -v RNAfold)" 2>&1 | awk '{printf "%s ", $1}')"
  python3 -c 'import RNA, sys; print("python", sys.version.split()[0], "RNA", getattr(RNA, "__version__", "unknown"))'
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"
grep -q "RNAfold 2.7.2" "$O/host.txt" || { echo "FAIL: not RNAfold 2.7.2"; exit 1; }

FAILS=0
phase() {
  echo "== $1 $(date -u +%H:%M:%S)"
  s=$(date +%s)
  python3 -u real.py "$@" "$O" 2>&1 | tee -a "$O/log.txt"
  rc=${PIPESTATUS[0]}
  printf '%s\t%s s\trc %s\n' "$1" "$(( $(date +%s) - s ))" "$rc" | tee -a "$O/times.txt"
  [ "$rc" = 0 ] || FAILS=$((FAILS+1))
}
phase prep /tmp/Rfam.seed.gz
phase fold "$T"
phase fold1 5000
phase check "$T"
phase enum "$T"

awk '/^(anon|file) /{print "cgroup_"$1, $2}' /sys/fs/cgroup/memory.stat 2>/dev/null >> "$O/host.txt"
echo "cgroup_memory_peak $(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$O/host.txt"
df -k /tmp | awk 'NR==2{print "tmp_used_kib", $3}' >> "$O/host.txt"
gzip -c "$O/fold.out" > "$O/fold.out.gz"
gzip -c "$O/fold2.out" > "$O/fold2.out.gz"
rm -f "$O/fold.out" "$O/fold2.out" "$O/head.out" "$O/head.fa" "$O/seed.fa" "$O/ref.tsv"
ls -la "$O"
echo "fails $FAILS"
[ "$FAILS" = 0 ] && echo "VIENNARNA-REAL OK"
exit "$FAILS"
