#!/usr/bin/env bash
# measurements/rnaseq-counts-real task 1 (subread image): featureCounts over the whole ERR188026
# alignment against the whole Ensembl 116 annotation, paired-end fragments, unstranded,
# multimappers not counted (the default), at <threads> and again at 1 thread. Writes the count tables, the summary and
# the checks to /tmp/out.
#   featurecounts.sh <threads>
# Glue is BusyBox-safe: bioconda's x86 image has no lscpu, perl, join or sha256sum --quiet.
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; W=/var/tmp/fc
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "featurecounts	$(featureCounts -v 2>&1 | grep -m1 featureCounts)"
  echo "featurecounts_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v featureCounts)" | tr -d ' ')"
  echo "openblas	not linked (featureCounts is interval overlap; no BLAS)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged inputs must match their pins =="
( cd /tmp && sha256sum -c inputs.sha256 ) > $O/pins-check.txt 2>&1 \
  || { cat $O/pins-check.txt; fail "a staged input does not match its pin"; }
cat $O/pins-check.txt

# featureCounts reads the annotation once per run; give both runs the same uncompressed copy.
gzip -dc /tmp/GRCh38.116.gtf.gz > $W/genes.gtf || fail "gunzip GTF"

run_fc() {   # <threads> <label>
  local t0 t1 pid hwm h
  t0=$(date +%s)
  featureCounts -T "$1" -p --countReadPairs -s 0 -t exon -g gene_id \
    -a $W/genes.gtf -o $W/fc_$2.txt /tmp/reads.bam > $O/featurecounts_$2.log 2>&1 &
  pid=$!; hwm=0
  # VmHWM is the kernel's own high-water mark, so a late read misses nothing but the last interval
  while kill -0 $pid 2>/dev/null; do
    h=$(awk '/^VmHWM/{print $2}' /proc/$pid/status 2>/dev/null); [ -n "$h" ] && hwm=$h
    sleep 1
  done
  wait $pid; local rc=$?
  t1=$(date +%s)
  echo "fc_$2_rc	$rc	wall_s	$(( t1 - t0 ))	vmhwm_kb	$hwm" | tee -a $O/timing.txt
  [ "$rc" -eq 0 ] || { tail -n 30 $O/featurecounts_$2.log; fail "featureCounts ($2) exited $rc"; }
  # gene_id <TAB> count, sorted -- the table the comparison reads
  awk -F'\t' 'NR>2{print $1"\t"$NF}' $W/fc_$2.txt | sort > $O/fc_counts_$2.tsv
}
run_fc "$T" "T$T"
run_fc 1 T1
cat /sys/fs/cgroup/memory.peak > $O/memory-peak.txt 2>/dev/null || echo unreadable > $O/memory-peak.txt
cp $W/fc_T$T.txt.summary $O/fc.summary
cp $O/fc_counts_T$T.tsv $O/fc_counts.tsv
cat $O/fc.summary

# ---- checks ----
s() { awk -F'\t' -v k="$1" '$1==k{print $2}' $O/fc.summary; }
st() { awk -F'|' -v k="$1" 'index($1,k){gsub(/[ \t%]/,"",$2); print $2; exit}' /tmp/star.Log.final.out; }
IN=$(st "Number of input reads"); UNIQ=$(st "Uniquely mapped reads number")
MULTI=$(st "Number of reads mapped to multiple loci"); MANY=$(st "Number of reads mapped to too many loci")
SUM=$(awk -F'\t' 'NR>1{s+=$2} END{print s+0}' $O/fc.summary)
SEC=$(s Unassigned_Secondary); ASG=$(s Assigned); NOF=$(s Unassigned_NoFeatures); AMB=$(s Unassigned_Ambiguity)
MM=$(s Unassigned_MultiMapping); UNM=$(s Unassigned_Unmapped)
SEC=${SEC:-0}; ASG=${ASG:-0}; NOF=${NOF:-0}; AMB=${AMB:-0}; MM=${MM:-0}; UNM=${UNM:-0}
GSUM=$(awk -F'\t' '{s+=$2} END{print s+0}' $O/fc_counts.tsv)
NGENE=$(wc -l < $O/fc_counts.tsv | tr -d ' ')
SHA_T=$(sha256sum $O/fc_counts_T$T.tsv | cut -d' ' -f1); SHA_1=$(sha256sum $O/fc_counts_T1.tsv | cut -d' ' -f1)
SUM_1=$(awk -F'\t' 'NR>1 && $1!="Status"{print}' $W/fc_T1.txt.summary | sha256sum | cut -d' ' -f1)
SUM_T=$(awk -F'\t' 'NR>1 && $1!="Status"{print}' $W/fc_T$T.txt.summary | sha256sum | cut -d' ' -f1)
{
  echo "star_input_pairs        $IN"
  echo "star_unique / multi / too_many  $UNIQ / $MULTI / $MANY"
  echo "fc_genes                $NGENE"
  echo "fc_assigned             $ASG"
  echo "fc_gene_sum             $GSUM   want == fc_assigned (EXACT)"
  echo "fc_unique_classes       $(( ASG + NOF + AMB ))   want == star_unique (EXACT: assigned + no_features + ambiguity is every uniquely mapped pair)"
  echo "fc_unmapped             $UNM   want == star unmapped + too_many $(( IN - UNIQ - MULTI )) (EXACT)"
  echo "fc_multimapping         $MM   want in [2, 10] x star_multi $MULTI (one record per alignment pair; NH is 2..10 under STAR's default outFilterMultimapNmax 10)"
  echo "fc_secondary_records    $SEC"
  echo "fc_T${T}_vs_T1_counts     $SHA_T $SHA_1   want identical (EXACT: thread count must not change a count)"
  echo "fc_T${T}_vs_T1_summary    $SUM_T $SUM_1   want identical"
  echo "fc_counts_sha256        $SHA_T"
} > $O/check.txt
cat $O/check.txt
[ "$NGENE" -gt 60000 ] || fail "only $NGENE genes"
[ "$GSUM" = "$ASG" ] || fail "gene counts sum to $GSUM, summary says $ASG assigned"
[ "$(( ASG + NOF + AMB ))" = "$UNIQ" ] || fail "assigned+no_features+ambiguity $(( ASG + NOF + AMB )) != STAR unique $UNIQ"
[ "$UNM" = "$(( IN - UNIQ - MULTI ))" ] || fail "unmapped $UNM != STAR unmapped + too-many $(( IN - UNIQ - MULTI ))"
[ "$MM" -ge "$(( 2 * MULTI ))" ] && [ "$MM" -le "$(( 10 * MULTI ))" ] || fail "multimapping records $MM outside [2, 10] x $MULTI"
[ "$SHA_T" = "$SHA_1" ] || fail "counts differ between -T $T and -T 1"
[ "$SUM_T" = "$SUM_1" ] || fail "summaries differ between -T $T and -T 1"
echo "FEATURECOUNTS CHECKS PASSED"
