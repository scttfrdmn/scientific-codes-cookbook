#!/usr/bin/env bash
# measurements/qc-aggregate-real, task 1: FastQC over a real eleven-file cohort (four assays,
# 446M reads), then an independent recount of every file and check_fastqc.pl.
#   fastqc.sh <fastqc threads>
# Everything the task keeps is written flat under /tmp/out, which the spec stages out whole.
set +e
set -uo pipefail
NT=${1:?fastqc threads}
export PATH=/opt/conda/bin:/usr/local/bin:$PATH HOME=/tmp TMPDIR=/tmp
mkdir -p /tmp/out /tmp/work/fqc
cd /tmp/fq

{
  echo "arch	$(uname -m)"
  echo "nproc	$(nproc)"
  lscpu 2>/dev/null | grep -E '^(Model name|Thread\(s\) per core|Core\(s\) per socket|NUMA node\(s\))' || true
  # the same facts without lscpu (BusyBox images): SMT siblings of cpu0, and the model string
  echo "cpu0_thread_siblings	$(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -iE '^model name' /proc/cpuinfo 2>/dev/null || true
  echo "openblas_corename	n/a (FastQC is a JVM tool and links no BLAS)"
  echo "fastqc	$(fastqc --version 2>&1 | head -1)"
  echo "java	$(java -version 2>&1 | head -1)"
  echo "tmpfs_kib	$(df -k /tmp | awk 'NR==2{print $2}')"
} > /tmp/out/host.txt 2>&1
cat /tmp/out/host.txt

echo "== staged inputs must be the pinned bytes, and five must be ENA's own =="
T0=$(date +%s)
for f in *.fastq.gz; do ( grep "  $f\$" /tmp/inputs.sha256 > /tmp/work/$f.pin; sha256sum -c /tmp/work/$f.pin > /tmp/work/$f.chk 2>&1 ) & done
wait
cat /tmp/work/*.chk > /tmp/out/inputs-check.txt
md5sum -c /tmp/ena.md5 >> /tmp/out/inputs-check.txt 2>&1; MD5RC=$?
cat /tmp/out/inputs-check.txt
NOK=$(grep -c ': OK$' /tmp/out/inputs-check.txt)
echo "input_check_s	$(( $(date +%s) - T0 ))	ok_lines $NOK (want 16: 11 sha256 + 5 ENA md5)" | tee -a /tmp/out/host.txt
[ "$NOK" -eq 16 ] && [ "$MD5RC" -eq 0 ] || { echo "FAIL: a staged input does not match its pin"; exit 1; }

CG=/sys/fs/cgroup
sampler() { : > /tmp/out/samples.tsv
  while : ; do
    printf '%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "$(awk '/^anon /{print $2; exit}' $CG/memory.stat 2>/dev/null || echo 0)" \
      "$(awk '/^usage_usec/{print $2; exit}' $CG/cpu.stat 2>/dev/null || echo 0)" \
      "$(df -k /tmp 2>/dev/null | awk 'NR==2{print $3}')" \
      "$(ls /tmp/work/fqc/ 2>/dev/null | grep -c '_fastqc.zip$')" >> /tmp/out/samples.tsv
    sleep 5
  done; }
sampler & SPID=$!

echo "== FastQC: one invocation over the cohort, $NT files at a time =="
T0=$(date +%s)
fastqc -t "$NT" --extract -o /tmp/work/fqc ./*.fastq.gz > /tmp/out/fastqc.log 2>&1; FRC=$?
FWALL=$(( $(date +%s) - T0 ))
kill "$SPID" 2>/dev/null
echo "fastqc_rc	$FRC" | tee -a /tmp/out/host.txt
echo "fastqc_wall_s	$FWALL" | tee -a /tmp/out/host.txt
[ "$FRC" -eq 0 ] || { tail -20 /tmp/out/fastqc.log; exit 1; }
# when each report landed (5 s resolution): the cohort's wall is its slowest file
awk -F'\t' -v t0="$T0" '$5>last{print "reports_done", $5, "at_s", $1-t0; last=$5}' /tmp/out/samples.tsv | tee /tmp/out/report-times.txt
awk -F'\t' '$2>m{m=$2} END{printf "fastqc_peak_anon_gib\t%.2f\n", m/2^30}' /tmp/out/samples.tsv | tee -a /tmp/out/host.txt
awk -F'\t' '$4>m{m=$4} END{printf "tmpfs_peak_used_gib\t%.2f\n", m/2^20}' /tmp/out/samples.tsv | tee -a /tmp/out/host.txt
awk -F'\t' 'NR==1{t=$1;u=$3} END{printf "fastqc_avg_busy_cores\t%.2f\n", ($3-u)/1e6/($1-t)}' /tmp/out/samples.tsv | tee -a /tmp/out/host.txt

for d in /tmp/work/fqc/*_fastqc; do
  s=$(basename "$d" _fastqc)
  cp "$d/fastqc_data.txt" "/tmp/out/$s.fastqc_data.txt"
  cp "$d/summary.txt" "/tmp/out/$s.summary.txt"
done
cp /tmp/work/fqc/*_fastqc.zip /tmp/out/

echo "== independent recount of every file (not part of the FastQC wall) =="
T0=$(date +%s)
for f in *.fastq.gz; do s=${f%.fastq.gz}; ( gzip -dc "$f" | perl /tmp/recount.pl "$s" > "/tmp/work/$s.recount" ) & done
wait
cat /tmp/work/*.recount | sort > /tmp/out/recount.tsv
echo "recount_wall_s	$(( $(date +%s) - T0 ))" | tee -a /tmp/out/host.txt
cat /tmp/out/recount.tsv

perl /tmp/check_fastqc.pl /tmp/cohort.tsv /tmp/out/recount.tsv /tmp/out > /tmp/out/smoke-check-fastqc.txt 2>&1; CRC=$?
cat /tmp/out/smoke-check-fastqc.txt
[ "$CRC" -eq 0 ] || { echo "FASTQC CHECK FAILED"; exit 1; }
echo "FASTQC CHECK PASSED"
