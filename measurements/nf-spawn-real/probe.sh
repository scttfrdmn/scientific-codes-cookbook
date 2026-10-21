#!/usr/bin/env bash
# Sizing probe (MUSCLE image): how long does the recipe's MUSCLE step, `muscle -align`, take on
# PF01255.25 as the family grows? Aligns the first 115, 230 and 460 of its 919 sequences on every
# vCPU, then 230 on 8 threads, so the full run's box and TTL come from a measured curve.
#   bash probe.sh <unaligned.fa>
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
IN=$1
O=/tmp/out
mkdir -p "$O"
cd /tmp
T=$(grep -c '^processor' /proc/cpuinfo)
echo "uname_m $(uname -m) vcpus $T $(muscle -version 2>&1 | head -n 1)" | tee "$O/host.txt"
run() {   # n threads
  awk -v n="$1" '/^>/{k++} k<=n' "$IN" > "sub$1.fa"
  s=$(date +%s)
  muscle -align "sub$1.fa" -output "aln$1-$2.fa" -threads "$2" > "muscle$1-$2.log" 2>&1
  rc=$?; e=$(date +%s)
  pk=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0)
  echo "n=$1 threads=$2 rc=$rc wall_s=$((e-s)) seqs_out=$(grep -c '^>' "aln$1-$2.fa" 2>/dev/null) memory_peak_kib=$((pk/1024))" | tee -a "$O/probe.txt"
  cp "muscle$1-$2.log" "$O/"
}
run 115 "$T"; run 230 "$T"; run 460 "$T"; run 230 8
echo done | tee -a "$O/probe.txt"
