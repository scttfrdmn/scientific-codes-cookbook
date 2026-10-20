#!/usr/bin/env bash
# Task 01: Nextclade on the whole query at T threads, and on the first SUB genomes at one thread
# (the invariance pair task 02 checks). The image is a Rust binary with no interpreter, and on x86
# a BusyBox userland, so this is shell only; every check needing a parser is in check.py (task 02).
#   nextclade.sh <threads> [query, default query.fasta.gz]
set +e
export PATH=/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
cd /tmp
T="$1"; export QUERY="${2:-query.fasta.gz}"   # a sizing probe passes sub.fasta.gz
mkdir -p out
: > out/nextclade.log; : > out/nextclade_sub.log
now() { if [ -n "${EPOCHREALTIME:-}" ]; then echo "$EPOCHREALTIME"; else date +%s; fi; }

{ echo "arch $(uname -m)"; echo "nproc $(nproc)"
  grep -m1 -iE "model name|cpu part" /proc/cpuinfo
  echo "threads_per_core_cpu0 $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
  grep MemTotal /proc/meminfo; } > out/host.txt
cat out/host.txt

echo "== staged bytes must match their pins =="
grep -E "  (query.fasta.gz|sub.fasta.gz|truth.tsv)$" inputs.sha256 > q.sha256; sha256sum -c q.sha256 || { echo "FAIL: query does not match its pin"; exit 1; }
grep -E "  dataset.tar$" dataset.sha256 > d.sha256; sha256sum -c d.sha256 || { echo "FAIL: dataset.tar does not match its pin"; exit 1; }
tar -xf dataset.tar
TAG="2026-09-07--17-10-15Z"
grep -q "\"$TAG\"" dataset/pathogen.json || { echo "FAIL: dataset does not declare tag $TAG"; exit 1; }
nextclade --version 2>&1 | head -1 | tee out/version.txt

echo "== whole query, $T threads =="
t0=$(now)
nextclade run -j "$T" --input-dataset dataset --output-tsv out/nextclade.tsv "$QUERY" > out/nextclade.log 2>&1
RC=$?
t1=$(now)
PEAK=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
echo "$t0 $t1" | awk -v rc="$RC" -v p="$PEAK" -v t="$T" '{printf "nextclade_full\tthreads\t%s\trc\t%s\twall_s\t%.1f\tcgroup_memory_peak_bytes\t%s\n", t, rc, $2-$1, p}' | tee out/timing.tsv
test "$RC" = "0" || { tail -20 out/nextclade.log; echo "FAIL: nextclade exited $RC"; exit 1; }

echo "== first SUB genomes, 1 thread =="
t0=$(now)
nextclade run -j 1 --input-dataset dataset --output-tsv out/nextclade_sub.tsv sub.fasta.gz > out/nextclade_sub.log 2>&1
RC=$?
t1=$(now)
echo "$t0 $t1" | awk -v rc="$RC" '{printf "nextclade_sub_t1\tthreads\t1\trc\t%s\twall_s\t%.1f\n", rc, $2-$1}' | tee -a out/timing.tsv
test "$RC" = "0" || { tail -20 out/nextclade_sub.log; echo "FAIL: nextclade (sub) exited $RC"; exit 1; }

NQ=$(gzip -dc "$QUERY" | grep -c '^>')
NR=$(( $(wc -l < out/nextclade.tsv) - 1 ))
printf 'query_records\t%s\nnextclade_rows\t%s\n' "$NQ" "$NR" | tee -a out/timing.tsv
test "$NQ" = "$NR" || { echo "FAIL: $NR rows for $NQ genomes"; exit 1; }
gzip -c out/nextclade.tsv > out/nextclade.tsv.gz && rm -f out/nextclade.tsv
echo "NEXTCLADE OK"
