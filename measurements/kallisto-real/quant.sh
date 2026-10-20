#!/usr/bin/env bash
# measurements/kallisto-real task 2 (kallisto image, every chip): the complete ERR188026 run against
# recipes/kallisto's index of all 465,769 Ensembl 116 transcripts, twice:
#   A  the default run, fragment-length distribution estimated from the reads (the real job, timed)
#   B  the same with the distribution given (-l 200 -s 30), which takes the one thread-order-
#      dependent input out of the EM, so B is a candidate fixed point across chips
# Each run is checked here for what the image can check without a hash join (BusyBox awk on x86):
# read bookkeeping and conservation. Ranks against salmon and RSEM, and the cross-chip comparison,
# are check.R in task 3.
#   quant.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out
mkdir -p $O
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
K=$(command -v kallisto)
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "kallisto	$(kallisto version 2>&1 | head -n 1)"
  echo "kallisto_elf_machine	$(od -An -tu2 -j18 -N2 "$K" | tr -d ' ')"
  echo "blas_libs_in_image	$(ls /opt/conda/lib /usr/local/lib 2>/dev/null | grep -ci blas)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged inputs: reads against ENA's md5, the index recorded (task 3 asserts it is one file) =="
( cd /tmp && md5sum -c reads.md5 ) > $O/pins-check.txt 2>&1 || { cat $O/pins-check.txt; fail "a read file does not match ENA's md5"; }
( cd /tmp && sha256sum kallisto.idx ) >> $O/pins-check.txt
cat $O/pins-check.txt

# Peak RSS: the kernel's own VmHWM for the kallisto process, read every second (monotonic, so a
# poll can only miss growth in its last second).
hwm() { while :; do
    for p in /proc/[0-9]*; do
      [ "$(cat $p/comm 2>/dev/null)" = kallisto ] && awk '/^VmHWM/{print $2}' $p/status 2>/dev/null >> $O/hwm-$1.txt
    done
    sleep 1
  done; }

run() {  # run <label> [extra kallisto args]
  L=$1; shift
  : > $O/hwm-$L.txt
  hwm $L & HP=$!
  T0=$(date +%s)
  kallisto quant -i /tmp/kallisto.idx -o $O/$L -t "$T" "$@" /tmp/ERR188026_1.fastq.gz /tmp/ERR188026_2.fastq.gz > $O/quant-$L.log 2>&1
  RC=$?
  T1=$(date +%s)
  kill $HP 2>/dev/null
  echo "quant_$L	rc	$RC	wall_s	$(( T1 - T0 ))	maxrss_kb	$(sort -n $O/hwm-$L.txt | tail -n 1)" | tee -a $O/timing.txt
  rm -f $O/hwm-$L.txt
  [ "$RC" = 0 ] || { tail -n 30 $O/quant-$L.log; fail "kallisto quant $L rc $RC"; }
  jget() { awk -F'[:,]' -v k="$1" '$0 ~ "\""k"\"" {gsub(/[^0-9]/,"",$2); print $2; exit}' $O/$L/run_info.json; }
  NT=$(jget n_targets); NP=$(jget n_processed); NA=$(jget n_pseudoaligned); NU=$(jget n_unique)
  # abundance.tsv prints with the stream default, 6 significant digits, so each printed value is
  # within 5e-6 of itself relative; a sum of printed values is within 5e-6 x that sum.
  awk -v L=$L -v nt=$NT -v np=$NP -v na=$NA -v nu=$NU 'NR>1{c+=$4; t+=$5; n++; if ($3>$2) el++}
    END{bc=5e-6*c; bt=5e-6*t
      printf "%s\tn_targets\t%d\t== 465769 (Ensembl 116 cDNA records)\t%s\n", L, nt, (nt==465769)?"PASS":"FAIL"
      printf "%s\trows\t%d\t== n_targets\t%s\n", L, n, (n==nt)?"PASS":"FAIL"
      printf "%s\tn_processed\t%d\t== 15800127 (ENA read_count)\t%s\n", L, np, (np==15800127)?"PASS":"FAIL"
      printf "%s\tn_pseudoaligned n_unique\t%d %d\tobs\tobs\n", L, na, nu
      printf "%s\tsum est_counts\t%.4f\t== n_pseudoaligned within %.2f (6-digit rounding)\t%s\n", L, c, bc, (c-na<=bc && na-c<=bc)?"PASS":"FAIL"
      printf "%s\tn_pseudoaligned - sum est_counts\t%.4f\tobs\tobs\n", L, na-c
      printf "%s\tsum tpm\t%.4f\t== 1e6 within %.2f\t%s\n", L, t, bt, (t-1e6<=bt && 1e6-t<=bt)?"PASS":"FAIL"
      printf "%s\teff_length > length\t%d\t== 0\t%s\n", L, el, (el==0)?"PASS":"FAIL"}' $O/$L/abundance.tsv | tee -a $O/check.txt
  ( cd $O && sha256sum $L/abundance.tsv ) >> $O/results.sha256
}

echo "== A: kallisto quant, $T threads, fragment lengths estimated =="
run A
echo "== B: kallisto quant, $T threads, -l 200 -s 30 =="
run B -l 200 -s 30
cat $O/results.sha256
grep -q FAIL $O/check.txt && fail "a check failed"
echo "KALLISTO CHECKS PASSED"
