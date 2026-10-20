#!/usr/bin/env bash
# measurements/rsem-real task 1 (bowtie2 image, Graviton4, run once): align the complete
# ERR188026 run to RSEM's own transcript FASTA with the bowtie2 command
# `rsem-calculate-expression --bowtie2 --paired-end` builds (RSEM 1.3.1), plus --reorder so the
# SAM's record order does not depend on thread scheduling. This image has no samtools, so the SAM
# travels gzip-compressed; RSEM's htslib reads it as is.
#   align.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; S=/tmp/sam; W=/var/tmp/bt2
mkdir -p $O $S $W
fail() { echo "FAIL: $*" | tee -a $O/pins.txt; exit 1; }
{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  for t in python3 samtools pigz bgzip; do echo "has_$t	$(command -v $t || echo no)"; done
  echo "bowtie2	$(bowtie2 --version 2>&1 | head -n 1)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged inputs against their pins =="
M1=$(md5sum /tmp/ERR188026_1.fastq.gz | cut -d' ' -f1)
M2=$(md5sum /tmp/ERR188026_2.fastq.gz | cut -d' ' -f1)
{
  echo "ERR188026_1.fastq.gz md5 $M1 want 5fd42c5e9889ac6f30259c04848c8ffb (ENA)"
  echo "ERR188026_2.fastq.gz md5 $M2 want 2f03cfb4d272170d0d6451523e37569c (ENA)"
} > $O/pins.txt
[ "$M1" = 5fd42c5e9889ac6f30259c04848c8ffb ] || fail "mate 1 md5"
[ "$M2" = 2f03cfb4d272170d0d6451523e37569c ] || fail "mate 2 md5"
WANT=$(awk '$2=="ens116.transcripts.fa"{print $1}' /tmp/ref.sha256)
GOT=$(sha256sum /tmp/ens116.transcripts.fa | cut -d' ' -f1)
echo "ens116.transcripts.fa sha256 $GOT want $WANT (task 0)" >> $O/pins.txt
[ -n "$WANT" ] && [ "$GOT" = "$WANT" ] || fail "RSEM transcript FASTA does not match task 0's pin"
cat $O/pins.txt

echo "== bowtie2-build on RSEM's transcript FASTA, $T threads =="
python3 -u /tmp/timed.py $O/timing.txt build $O/bowtie2-build.log -- \
  bowtie2-build --threads "$T" /tmp/ens116.transcripts.fa $W/ens116 || { tail -n 30 $O/bowtie2-build.log; fail "bowtie2-build"; }

if command -v pigz >/dev/null; then Z="pigz -p 4 -1"; else Z="gzip -1"; fi
echo "compressor	$Z" >> $O/host.txt
echo "== bowtie2, RSEM's paired-end settings, -k 200, $T threads =="
cpu() { awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null; }
C0=$(cpu); A0=$(date +%s)
python3 -u /tmp/timed.py $O/timing.txt align $O/bowtie2.log -- bash -c "set -o pipefail
  bowtie2 -q --phred33 --sensitive --dpad 0 --gbar 99999999 --mp 1,1 --np 1 --score-min L,0,-0.1 \
    -I 1 -X 1000 --no-mixed --no-discordant -p $T -k 200 --reorder -x $W/ens116 \
    -1 /tmp/ERR188026_1.fastq.gz -2 /tmp/ERR188026_2.fastq.gz | $Z > $S/aln.sam.gz" \
  || { tail -n 30 $O/bowtie2.log; fail "bowtie2"; }
C1=$(cpu); A1=$(date +%s)
# avg cores busy over the align step: well below $T means the single-threaded compressor was the ceiling
echo "align_avg_cores	$(awk -v a="$C0" -v b="$C1" -v s="$A0" -v e="$A1" 'BEGIN{ if (e > s && b > a) printf "%.2f", (b-a)/1e6/(e-s); else print "unread" }')" >> $O/timing.txt
cat $O/bowtie2.log $O/timing.txt
PAIRS=$(awk '/reads; of these/{print $1; exit}' $O/bowtie2.log)
echo "bowtie2_pairs $PAIRS want 15800127 (ENA read_count)" | tee -a $O/pins.txt
[ "$PAIRS" = 15800127 ] || fail "bowtie2 read $PAIRS pairs"
ls -la $S
( cd $S && sha256sum aln.sam.gz ) > $O/sam.sha256
cat $O/sam.sha256
echo "ALIGN OK"
