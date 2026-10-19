#!/usr/bin/env bash
# bismark-real, task 1 (Bismark image): align the whole RRBS run to GRCh38 and extract the calls --
# the recipe's `bismark align` + `bismark extract`, at a whole sample's depth.
#
#   bash align-bismark.sh <threads> <reads|0 for all> <reads in the file> <mode: p|mc> [<mode> ...]
#
# mode p  = one Bismark instance, 2 bowtie2 processes x threads/2 (-p)
# mode mc = --multicore threads/4 chunks, each 2 bowtie2 x 2 threads
# Every mode named is run in turn into /tmp/out/<mode>/, so the probe can show the two give the
# same calls before the whole run trusts either. Needs /tmp/genome/<fa> + Bisulfite_Genome/ and
# /tmp/ENCFF000LYO.trimmed.fastq.gz (prep-trim.sh). Runs on aarch.bio's arm64 image and bioconda's x86 one (BusyBox
# userland: no sha256sum --quiet, no date +%N), so the glue is POSIX sh and awk. Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp
T=$1 UPTO=$2 NFQ=$3; shift 3
FQ=/tmp/ENCFF000LYO.trimmed.fastq.gz
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-52s %-34s %s\n' "$1" "$2" "$3" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }

echo "== host ==" | tee "$O/host.txt"
{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "bismark $(bismark --version 2>&1 | grep -m1 .)"
  echo "bowtie2 $(bowtie2 --version 2>&1 | grep -m1 version)"
} | tee -a "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" | tee -a "$O/host.txt"

echo "== the index the prep built, by sha256 =="
( cd genome && sha256sum -c /tmp/index.sha256 > /tmp/index.check 2>&1 ) \
  || { cat /tmp/index.check; echo "FAIL: index does not match its pin"; exit 1; }
echo "index: $(grep -c ': OK$' /tmp/index.check) files match"

UARG=""; [ "$UPTO" != 0 ] && UARG="-u $UPTO"
for MODE in "$@"; do
  M=$O/$MODE; mkdir -p "$M"
  case $MODE in
    p)  PAR="-p $((T/2))" ;;
    mc) PAR="--multicore $((T/4)) -p 2" ;;
    *)  echo "unknown mode $MODE"; exit 1 ;;
  esac
  W=/var/tmp/aln-$MODE; mkdir -p "$W"
  echo "== $MODE: bismark align ($PAR $UARG) =="
  # footprint every 5 s: anonymous memory (RSS, not page cache or tmpfs) and cumulative cgroup CPU
  ( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$M/samples-align.txt"; sleep 5; done ) &
  SPID=$!
  T0=$(date +%s)
  bismark align --genome /tmp/genome --single_end "$FQ" $PAR $UARG -o "$W" --temp_dir "$W" > "$M/align.log" 2>&1
  RC=$?
  T1=$(date +%s)
  kill $SPID 2>/dev/null
  echo "$MODE align_rc $RC align_wall_s $((T1-T0))" | tee -a "$O/times.txt"
  awk -v m="$MODE" 'NR==1{t0=$1; u0=$3} $2>x{x=$2} {t=$1; u=$3} END{printf "%s align_peak_anon_gib %.2f align_avg_busy_cores %.2f\n", m, x/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$M/samples-align.txt" | tee -a "$O/times.txt"
  if [ "$RC" != 0 ]; then tail -30 "$M/align.log"; echo "FAIL: $MODE align exit $RC"; FAILS=$((FAILS+1)); continue; fi
  BAM=$(ls "$W"/*_bismark_bt2.bam | head -1)
  REP=$(ls "$W"/*_bismark_bt2_SE_report.txt | head -1)
  cp "$REP" "$M/SE_report.txt"

  echo "== $MODE: bismark extract =="
  E=$W/ext; mkdir -p "$E"
  ( while : ; do echo "$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)" >> "$M/samples-extract.txt"; sleep 5; done ) &
  SPID=$!
  T2=$(date +%s)
  bismark extract -s --comprehensive --bedGraph --gzip -o "$E" "$BAM" > "$M/extract.log" 2>&1
  RC=$?
  T3=$(date +%s)
  kill $SPID 2>/dev/null
  echo "$MODE extract_rc $RC extract_wall_s $((T3-T2))" | tee -a "$O/times.txt"
  awk -v m="$MODE" 'NR==1{t0=$1; u0=$3} $2>x{x=$2} {t=$1; u=$3} END{printf "%s extract_peak_anon_gib %.2f extract_avg_busy_cores %.2f\n", m, x/1073741824, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$M/samples-extract.txt" | tee -a "$O/times.txt"
  if [ "$RC" != 0 ]; then tail -30 "$M/extract.log"; echo "FAIL: $MODE extract exit $RC"; FAILS=$((FAILS+1)); continue; fi
  cp "$E"/*_splitting_report.txt "$M/splitting_report.txt"
  cp "$E"/*.M-bias.txt "$M/M-bias.txt" 2>/dev/null
  cp "$E"/*.bismark.cov.gz "$M/CpG.cov.gz"
  du -sk "$W" "$E" | tee -a "$O/times.txt"

  echo "== $MODE: identities =="
  # value of a "label:<TAB>value" line in a Bismark report
  rv() { awk -F'\t' -v k="$2" '$1==k{gsub(/%/,"",$2); print $2; exit}' "$1"; }
  R=$M/SE_report.txt S=$M/splitting_report.txt
  IN=$(rv "$R" "Sequences analysed in total:")
  UNI=$(rv "$R" "Number of alignments with a unique best hit from the different alignments:")
  NONE=$(rv "$R" "Sequences with no alignments under any condition:")
  AMB=$(rv "$R" "Sequences did not map uniquely:")
  DISC=$(rv "$R" "Sequences which were discarded because genomic sequence could not be extracted:")
  WANT=$UPTO; [ "$UPTO" = 0 ] && WANT=$NFQ
  check "$([ "$IN" = "$WANT" ] && echo 1)" "reads analysed == reads in" "want $WANT" "$IN"
  check "$([ $((UNI+NONE+AMB)) = "$IN" ] && echo 1)" "unique + none + ambiguous == analysed" "ACCOUNTING" "$UNI+$NONE+$AMB = $((UNI+NONE+AMB))"
  # "discarded" is a subset of the unique hits: aligned, but too close to a contig end for the
  # context to be read, so never written
  STR=$(awk -F': ' '/^Total number of methylation call strings processed/{print $2}' "$S")
  check "$([ "$STR" = $((UNI-DISC)) ] && echo 1)" "extract: call strings == unique - discarded" "every written alignment extracted" "$STR / $UNI - $DISC"
  # the C counts the aligner reported from its XM strings vs those the extractor re-read from the BAM
  for ctx in CpG CHG CHH; do
    AM=$(rv "$R" "Total methylated C's in $ctx context:");   SM=$(rv "$S" "Total methylated C's in $ctx context:")
    AU=$(rv "$R" "Total unmethylated C's in $ctx context:"); SU=$(rv "$S" "Total C to T conversions in $ctx context:")
    check "$([ -n "$AM" ] && [ "$AM" = "$SM" ] && [ "$AU" = "$SU" ] && echo 1)" "$ctx: align report == splitting report" "meth/unmeth CONSERVED" "$AM/$AU vs $SM/$SU"
    echo "$MODE $ctx meth $AM unmeth $AU" >> "$O/contexts.txt"
  done
  AT=$(rv "$R" "Total number of C's analysed:"); ST=$(rv "$S" "Total number of C's analysed:")
  check "$([ "$AT" = "$ST" ] && echo 1)" "C's analysed: align == extract" "CONSERVED" "$AT / $ST"
  # the coverage file is the per-cytosine form of the extractor's CpG totals
  CPGM=$(rv "$S" "Total methylated C's in CpG context:"); CPGU=$(rv "$S" "Total C to T conversions in CpG context:")
  gzip -dc "$M/CpG.cov.gz" | awk '{m+=$5; u+=$6; n++; k=$1 SUBSEP $2; if (k in seen) d++; seen[k]=1} END{printf "%.0f %.0f %.0f %.0f\n", n, m, u, d+0}' > "$M/cov-sums.txt"
  read -r CN CM CU CD < "$M/cov-sums.txt"
  check "$([ "$CM" = "$CPGM" ] && [ "$CU" = "$CPGU" ] && echo 1)" "sum(cov) == CpG totals" "bedGraph step CONSERVES calls" "$CM/$CU vs $CPGM/$CPGU"
  check "$([ "$CD" = 0 ] && echo 1)" "cov: one row per cytosine" "no duplicate chr:pos" "$CD duplicates of $CN"
  echo "$MODE cpg_positions $CN mapping_efficiency $(rv "$R" "Mapping efficiency:")% unique $UNI ambiguous $AMB none $NONE" | tee -a "$O/times.txt"
  for ctx in CpG CHG CHH; do echo "$MODE $ctx $(rv "$S" "C methylated in $ctx context:")%"; done | tee -a "$O/times.txt"
  # fingerprint of the calls, for the cross-chip comparison: rows are sorted by the extractor
  gzip -dc "$M/CpG.cov.gz" | sha256sum | awk -v m="$MODE" '{print m, "cov_sha256", $1}' | tee -a "$O/fingerprints.txt"
  mv "$BAM" "$M/bismark.bam"
  rm -rf "$W"
done

if [ $# -ge 2 ]; then
  a=$1 b=$2
  check "$(cmp -s "$O/$a/CpG.cov.gz" "$O/$b/CpG.cov.gz" || gzip -dc "$O/$a/CpG.cov.gz" | cmp -s - <(gzip -dc "$O/$b/CpG.cov.gz") && echo 1)" "calls: mode $a == mode $b" "chunking/threads do not move a call" "cov compared"
  check "$(cmp -s "$O/$a/SE_report.txt" "$O/$b/SE_report.txt" || diff <(grep -v -e '^Bismark completed' -e '^Bismark report' -e '^Bismark was run with' "$O/$a/SE_report.txt") <(grep -v -e '^Bismark completed' -e '^Bismark report' -e '^Bismark was run with' "$O/$b/SE_report.txt") >/dev/null && echo 1)" "align report: mode $a == mode $b" "same counts" "report compared"
fi
echo "failures $FAILS" | tee -a "$O/checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "BISMARK OK"
