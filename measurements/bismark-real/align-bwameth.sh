#!/usr/bin/env bash
# bismark-real, task 3 (bwa-meth image): the second aligner on the same reads -- the recipe's
# 04-bwameth-align, at a whole sample's depth. bwa-meth bundles bwa and samtools (its own conda
# dependencies), so align, sort and index are one task.
#
#   bash align-bwameth.sh <threads> <reads|0 for all> <reads in the file> <bwa -T>
#
# bwa-meth calls `bwa mem -T 40`: it drops any alignment scoring under 40, and a 36 bp read
# scores at most 36. So on this library the default maps NOTHING, and says nothing about it. The
# task first shows that on the first 100,000 reads, then runs the whole sample with the -T given
# (passed through to bwa, whose last -T wins). Needs /tmp/ref.fa.bwameth.c2t.{amb,ann,bwt,pac,sa}
# and /tmp/ENCFF000LYO.trimmed.fastq.gz (prep-trim.sh). Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp
# staged files carry stage-in mtimes, not build order; bwa-meth otherwise refuses an index whose
# files look older than the (absent) converted FASTA
export BWA_METH_SKIP_TIME_CHECKS=1
T=$1 UPTO=$2 NFQ=$3 MINSCORE=$4
FQ=/tmp/ENCFF000LYO.trimmed.fastq.gz
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-52s %-34s %s\n' "$1" "$2" "$3" | tee -a "$O/checks-bwameth.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  echo "bwameth $(bwameth.py --version 2>&1 | grep -v Warning | grep -m1 .)"
  echo "bwa $(bwa 2>&1 | grep -m1 Version)"
  echo "samtools $(samtools --version | sed -n 1p)"
} | tee "$O/host-bwameth.txt"
REF=/tmp/ref.fa   # only the name: bwa reads ref.fa.bwameth.c2t.*

echo "== bwa-meth's default on 36 bp reads: the first 100,000 =="
gzip -dc "$FQ" | awk 'NR<=400000' > /var/tmp/head.fq
bwameth.py --reference "$REF" -t "$T" /var/tmp/head.fq 2> "$O/bwameth-default.log" | samtools view -c -F 0x904 - > /var/tmp/n.default
RD="${PIPESTATUS[0]}/${PIPESTATUS[1]}"
bwameth.py --reference "$REF" -t "$T" "-T$MINSCORE" /var/tmp/head.fq 2> "$O/bwameth-T.log" | samtools view -c -F 0x904 - > /var/tmp/n.T
RT="${PIPESTATUS[0]}/${PIPESTATUS[1]}"
ND=$(cat /var/tmp/n.default) NT=$(cat /var/tmp/n.T)
check "$([ "$RD" = 0/0 ] && [ "$RT" = 0/0 ] && echo 1)" "head runs: bwa-meth | samtools exit" "0/0 both (a crash is not a zero)" "$RD, $RT"
echo "first_100000 mapped_default_T40 $ND mapped_T$MINSCORE $NT" | tee -a "$O/times-bwameth.txt"
check "$([ "$ND" = 0 ] && echo 1)" "bwa-meth default -T 40 on 36 bp reads" "maps 0 (max score 36)" "$ND of 100000"
check "$([ "$NT" -gt 0 ] && echo 1)" "bwa-meth -T $MINSCORE on 36 bp reads" "maps some" "$NT of 100000"

echo "== bwa-meth, -T $MINSCORE, the whole run =="
IN=$FQ
if [ "$UPTO" != 0 ]; then gzip -dc "$FQ" | awk -v n=$((UPTO*4)) 'NR<=n' > /var/tmp/upto.fq; IN=/var/tmp/upto.fq; fi
( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$O/samples-bwameth.txt"; sleep 5; done ) &
SPID=$!
# a heartbeat in the task's own log, so a run that dies says how far it got
( while sleep 120; do echo "heartbeat $(date +%s) anon_gib $(awk '/^anon /{printf "%.1f", $2/1073741824; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) bwa_batches $(grep -c 'Processed' "$O/bwameth.log" 2>/dev/null)"; done ) &
HPID=$!
T0=$(date +%s)
# -K fixes bwa's batch at 20 Mbp whatever the thread count. bwa's default batch is 10 Mbp per
# thread, and on a three-letter genome a batch of short reads costs ~0.2 GiB per Mbp: at 16
# threads that is 160 Mbp and more memory than the box. It also makes the batching, and so the
# output, independent of -t. The timeout lets a stalled run stage its logs out.
timeout 5400 bwameth.py --reference "$REF" -t "$T" "-T$MINSCORE" -K20000000 "$IN" 2> "$O/bwameth.log" \
  | samtools view -b -o /var/tmp/bwm.unsorted.bam - 2> "$O/view.log"
RC="${PIPESTATUS[0]}${PIPESTATUS[1]}"; [ "$RC" = 00 ] && RC=0
T1=$(date +%s)
samtools sort -@ "$T" -m 1G -T /var/tmp/srt -o "$O/bwameth.sorted.bam" /var/tmp/bwm.unsorted.bam 2> "$O/sort.log" \
  && samtools index -@ "$T" "$O/bwameth.sorted.bam" 2>> "$O/sort.log"
RC2=$?
T2=$(date +%s)
kill $SPID $HPID 2>/dev/null
echo "align_rc $RC align_wall_s $((T1-T0)) sort_rc $RC2 sort_index_wall_s $((T2-T1))" | tee -a "$O/times-bwameth.txt"
awk 'NR==1{t0=$1; u0=$3} $2>x{x=$2} {t=$1; u=$3} END{printf "peak_anon_gib %.2f avg_busy_cores %.2f\n", x/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples-bwameth.txt" | tee -a "$O/times-bwameth.txt"
check "$([ "$RC" = 0 ] && [ "$RC2" = 0 ] && [ -s "$O/bwameth.sorted.bam.bai" ] && echo 1)" "bwa-meth + sort + index" "exit 0" "$RC/$RC2"
[ "$RC" = 0 ] || tail -20 "$O/bwameth.log"

B=$O/bwameth.sorted.bam
WANT=$UPTO; [ "$UPTO" = 0 ] && WANT=$NFQ
PRIM=$(samtools view -c -F 0x900 "$B")
MAP=$(samtools view -c -F 0x904 "$B")
QC=$(samtools view -c -F 0x904 -f 0x200 "$B")
Q10=$(samtools view -c -F 0xF04 -q 10 "$B")
XM=$(samtools view "$B" | awk '/\tXM:Z:/{n++} END{print n+0}')
check "$([ "$PRIM" = "$WANT" ] && echo 1)" "primary records == reads in" "every read reported once" "$PRIM / $WANT"
check "$([ "$XM" = 0 ] && echo 1)" "XM-tagged reads" "0 -- Bismark's extractor cannot read this BAM" "$XM"
echo "reads $PRIM mapped $MAP qcfail_mapped $QC mapped_q10_pass $Q10" | tee -a "$O/times-bwameth.txt"
samtools view -F 0x904 "$B" | cut -f1-6,10 | LC_ALL=C sort | sha256sum | awk '{print "alignments_sha256", $1}' | tee "$O/fingerprints-bwameth.txt"
echo "failures $FAILS" | tee -a "$O/checks-bwameth.txt"
[ "$FAILS" = 0 ] || exit 1
echo "BWAMETH OK"
