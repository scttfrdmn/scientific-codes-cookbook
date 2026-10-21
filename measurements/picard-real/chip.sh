#!/usr/bin/env bash
# Task body, staged to the bucket and sha256-checked by its spec (spawn user data is capped at 16 KiB).
# == topology and BLAS kernel: diagnostic only, never fails the task ==
( echo "topo_model $(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_siblings $(awk -F': ' '/^siblings/{print $2; exit}' /proc/cpuinfo) topo_cpu_cores $(awk -F': ' '/^cpu cores/{print $2; exit}' /proc/cpuinfo)"
  echo "topo_cpu0_thread_siblings $(cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
  echo "topo_lscpu_threads_per_core $(lscpu 2>/dev/null | awk -F: '/per core/{gsub(/ /,"",$2); print $2}')"
  for b in ; do p=/opt/conda/bin/$b; [ -x "$p" ] || continue
    echo "blas_link $b: $(ldd "$p" 2>/dev/null | grep -i -E 'blas|lapack' | awk '{print $1}' | tr '\n' ' ')"
    OPENBLAS_VERBOSE=2 "$p" --version 2>&1 | grep -i -E '^core' | sed "s/^/openblas_core $b: /"
  done
  ls /opt/conda/lib/ 2>/dev/null | grep -i openblas | sed 's/^/openblas_lib /' ) 2>&1 || true
set -eo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp LC_ALL=C
cd /tmp
echo "== MarkDuplicates on bwa's whole-genome BAM: 48,817,006 records, 4.5 GB =="
df -m /tmp | awk 'NR==2{print "tmpfs_total_mb "$2}'
# MarkDuplicates spills read-ends to TMPDIR, which on this path is tmpfs -- RAM, competing with its
# own Java heap. Sample the high-water mark: that is the constraint, as it was for samtools sort.
( MAX=0; while :; do U=$(df -m /tmp | awk 'NR==2{print $3}')
    [ "$U" -gt "$MAX" ] && { MAX=$U; echo "$MAX" > /tmp/.peak; }; sleep 2; done ) & SAMPLER=$!
T0=$(date +%s)
picard -Xmx24g MarkDuplicates I=aln.sorted.bam O=marked.bam M=dup_metrics.txt \
  TMP_DIR=/tmp QUIET=true VERBOSITY=ERROR 2> md.log
T1=$(date +%s); WALL=$(( T1 - T0 ))
kill "$SAMPLER" 2>/dev/null || true
PEAK=$(cat /tmp/.peak 2>/dev/null || echo 0)

# Tab-delimited, and the LIBRARY value is "Unknown Library" -- WITH A SPACE -- so default awk field
# splitting shifts every column by one and silently reports optical duplicates larger than total
# duplicates. -F'\t' is load-bearing, not style.
V() { awk -F'\t' -v want="$1" '/^LIBRARY/{for(i=1;i<=NF;i++) if($i==want) c=i; getline; print $c; exit}' dup_metrics.txt; }
UNP=$(V UNPAIRED_READS_EXAMINED); PAIRS=$(V READ_PAIRS_EXAMINED)
SECSUP=$(V SECONDARY_OR_SUPPLEMENTARY_RDS); UNMAP=$(V UNMAPPED_READS)
UNPD=$(V UNPAIRED_READ_DUPLICATES); RPD=$(V READ_PAIR_DUPLICATES)
OPT=$(V READ_PAIR_OPTICAL_DUPLICATES); PCT=$(V PERCENT_DUPLICATION); LIBSZ=$(V ESTIMATED_LIBRARY_SIZE)

# Conservation: picard partitions every input record into exactly four buckets, so they must sum to
# the input count. This proves no record was dropped WITHOUT needing samtools in this image -- and
# the first two reconcile independently with samtools flagstat on the same BAM.
EXAMINED=$(awk -v p="$PAIRS" -v u="$UNP" 'BEGIN{printf "%.0f", 2*p+u}')
TOTAL=$(awk -v e="$EXAMINED" -v s="$SECSUP" -v m="$UNMAP" 'BEGIN{printf "%.0f", e+s+m}')
# PERCENT_DUPLICATION is a function of counts picard itself reports: recomputing it is free.
PCT_CALC=$(awk -v d="$RPD" -v p="$PAIRS" -v ud="$UNPD" -v u="$UNP" \
  'BEGIN{den=2*p+u; num=2*d+ud; printf "%.6f", (den>0? num/den : 0)}')
PCT_DIFF=$(awk -v a="$PCT" -v b="$PCT_CALC" 'BEGIN{d=a-b; if(d<0)d=-d; printf "%.6f", d}')
MAGIC=$(od -An -tx1 -N4 marked.bam | tr -d ' \n')

{
  echo "reads_examined         $EXAMINED   = 2*$PAIRS pairs + $UNP unpaired"
  echo "                                 == samtools flagstat primary-mapped (48184934)"
  echo "secondary_supplementary $SECSUP   == samtools flagstat supplementary (519020)"
  echo "unmapped               $UNMAP"
  echo "partition_total        $TOTAL   want exactly 48817006 (every input record accounted for)"
  echo "read_pair_duplicates   $RPD"
  echo "optical_duplicates     $OPT"
  echo "percent_duplication    $PCT   (picard)"
  echo "percent_recomputed     $PCT_CALC   from the counts picard reports"
  echo "percent_abs_diff       $PCT_DIFF   want < 0.000001 (identity)"
  echo "estimated_library_size $LIBSZ"
  echo "bam_magic              $MAGIC   want 1f8b0804"
  echo "wall_s                 $WALL"
  echo "tmpfs_peak_used_mb     $PEAK   <- temp spill lands in RAM on this path"
} > smoke-check.txt
cat smoke-check.txt
test "$TOTAL" = '48817006'
test "$EXAMINED" = '48184934'
test "$SECSUP" = '519020'
test "$MAGIC" = '1f8b0804'
awk -v d="$PCT_DIFF" 'BEGIN{exit !(d<0.000001)}'
echo 'SMOKE CHECK PASSED'
