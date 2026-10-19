#!/usr/bin/env bash
# bismark-real, task 2 (samtools image): coordinate-sort and index Bismark's read-order BAM, which
# MethylDackel needs -- the recipe's 02-sort-index, at a whole sample's depth.
#
#   bash sort.sh <threads> <mode> [<mode> ...]
#
# Reads /tmp/<mode>/bismark.bam and /tmp/<mode>/SE_report.txt; writes /tmp/out/<mode>/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/var/tmp HOME=/tmp
T=$1; shift
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-52s %-34s %s\n' "$1" "$2" "$3" | tee -a "$O/checks-sort.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }
echo "samtools $(samtools --version | sed -n 1p)" | tee "$O/host-sort.txt"
echo "uname_m $(uname -m)" | tee -a "$O/host-sort.txt"

for MODE in "$@"; do
  M=$O/$MODE; mkdir -p "$M"
  T0=$(date +%s)
  samtools sort -@ "$T" -m 1G -T /var/tmp/srt-$MODE -o "$M/bismark.sorted.bam" "$MODE/bismark.bam" > "$M/sort.log" 2>&1
  RC=$?
  samtools index -@ "$T" "$M/bismark.sorted.bam" >> "$M/sort.log" 2>&1
  T1=$(date +%s)
  echo "$MODE sort_rc $RC sort_index_wall_s $((T1-T0))" | tee -a "$O/times-sort.txt"
  check "$([ "$RC" = 0 ] && [ -s "$M/bismark.sorted.bam.bai" ] && echo 1)" "$MODE: sorted + indexed" "exit 0, .bai present" "$RC"
  UNI=$(awk -F'\t' '$1=="Number of alignments with a unique best hit from the different alignments:"{u=$2} $1=="Sequences which were discarded because genomic sequence could not be extracted:"{d=$2} END{print u-d}' "$MODE/SE_report.txt")
  REC=$(samtools view -c "$M/bismark.sorted.bam")
  PRI=$(samtools view -c -F 0x904 "$M/bismark.sorted.bam")
  check "$([ "$REC" = "$UNI" ] && [ "$PRI" = "$UNI" ] && echo 1)" "$MODE: BAM records == unique - discarded" "all primary, mapped, one per read" "$REC records, $PRI primary / $UNI"
  # MAPQ histogram: what a MAPQ filter downstream would remove from Bismark's unique set
  samtools view "$M/bismark.sorted.bam" | awk -F'\t' '{q[$5]++} END{for (k in q) print k, q[k]}' | sort -n > "$M/mapq.txt"
  awk '{n+=$2; if ($1<10) lo+=$2} END{printf "mapq_lt10 %d of %d\n", lo, n}' "$M/mapq.txt" | tee -a "$O/times-sort.txt"
  # the alignments themselves, order-free, for the mode and cross-chip comparisons
  samtools view "$M/bismark.sorted.bam" | cut -f1-6,10 | LC_ALL=C sort | sha256sum | awk -v m="$MODE" '{print m, "alignments_sha256", $1}' | tee -a "$O/fingerprints-sort.txt"
done
if [ $# -ge 2 ]; then
  check "$([ "$(awk -v m="$1" '$1==m{print $3}' "$O/fingerprints-sort.txt")" = "$(awk -v m="$2" '$1==m{print $3}' "$O/fingerprints-sort.txt")" ] && echo 1)" \
    "alignments: mode $1 == mode $2" "QNAME..CIGAR,SEQ identical" "sorted multiset"
fi
echo "failures $FAILS" | tee -a "$O/checks-sort.txt"
[ "$FAILS" = 0 ] || exit 1
echo "SORT OK"
