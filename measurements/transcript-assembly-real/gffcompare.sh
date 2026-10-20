#!/usr/bin/env bash
# measurements/transcript-assembly-real task 3 (gffcompare image, every chip): score this chip's
# de novo and guided assemblies against the whole Ensembl 116 annotation, and hold gffcompare's
# counts against the ones check_st.py computed independently from the same files.
#   gffcompare.sh
# No python in this image; the glue is awk and sort only, BusyBox-safe, and holds no large arrays.
set +e
set -uo pipefail
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp LC_ALL=C
O=/tmp/out; W=/var/tmp/gc
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; FAILED=1; }
FAILED=0
echo "gffcompare	$(gffcompare --version 2>&1 | head -n 1)" > $O/host.txt
echo "uname_m	$(uname -m)" >> $O/host.txt
( cd /tmp && sha256sum -c ref.sha256 ) || { echo "FAIL: annotation does not match its pin"; exit 1; }
gzip -dc /tmp/GRCh38.116.gtf.gz > $W/genes.gtf || { echo "FAIL: gunzip"; exit 1; }

j() {   # <assembly> <key> from check_st.py's check.json (one key per line, sort_keys)
  awk -v a="\"$1\"" -v k="\"$2\"" '$1==a":"{on=1; next} on && $1==k":"{gsub(/[,]/,"",$2); print $2; exit}' /tmp/check.json
}
for m in denovo guided; do
  t0=$(date +%s)
  gffcompare -r $W/genes.gtf -o $O/gc_$m /tmp/${m}_p8.gtf > $O/gffcompare_$m.log 2>&1 \
    || { cat $O/gffcompare_$m.log; echo "FAIL: gffcompare $m"; exit 1; }
  t1=$(date +%s)
  gffcompare -R -r $W/genes.gtf -o $O/gcR_$m /tmp/${m}_p8.gtf > $O/gffcompareR_$m.log 2>&1 \
    || { cat $O/gffcompareR_$m.log; echo "FAIL: gffcompare -R $m"; exit 1; }
  echo "gffcompare_${m}_wall_s	$(( t1 - t0 ))" >> $O/timing.txt
  # gffcompare writes .tmap/.refmap beside the QUERY file, i.e. in /tmp; keep copies in the outputs
  cp /tmp/gc_$m.${m}_p8.gtf.tmap /tmp/gc_$m.${m}_p8.gtf.refmap /tmp/gcR_$m.${m}_p8.gtf.tmap $O/
  TM=$O/gc_$m.${m}_p8.gtf.tmap
  sed -n '1,40p' $O/gc_$m.stats

  NQ=$(j ${m}_p8 transcripts)
  ROWS=$(awk 'NR>1' "$TM" | wc -l | tr -d ' ')
  QM=$(awk '/Query mRNAs/{print $5; exit}' $O/gc_$m.stats)
  EQ_MULTI=$(awk -F'\t' 'NR>1 && $3=="=" && $6>1' "$TM" | wc -l | tr -d ' ')
  CHAIN=$(j ${m}_p8 multi_exon_chain_matches)
  # intron level, recomputed: precision = annotated / distinct assembled introns (check_st, its own
  # parse of both GTFs); sensitivity = the same numerator over gffcompare's reference intron count
  AI=$(j ${m}_p8 annotated_introns); DI=$(j ${m}_p8 distinct_introns)
  QI=$(awk '/Novel introns:/{split($3,a,"/"); print a[2]; exit}' $O/gc_$m.stats)
  RI=$(awk '/Missed introns:/{split($3,a,"/"); print a[2]; exit}' $O/gc_$m.stats)
  IL=$(awk '/ Intron level:/{print $3, $5; exit}' $O/gc_$m.stats)
  ILX=$(awk -v a="$AI" -v d="$DI" -v r="$RI" 'BEGIN{printf "%.1f %.1f", 100*a/r, 100*a/d}')
  # which transcripts the two '=' counts disagree on, with gffcompare's own row for each
  awk -F'\t' 'NR>1 && $3=="=" && $6>1{print $5}' "$TM" | sort > $W/eq_$m.ids
  comm -3 $W/eq_$m.ids /tmp/${m}_p8.chainmatch.ids | tr -d '\t' > $O/gc_$m.eq_disagree.txt
  awk -F'\t' 'NR==FNR{k[$1]=1; next} ($5 in k)' $O/gc_$m.eq_disagree.txt "$TM" > $O/gc_$m.eq_disagree.tmap
  awk -F'\t' 'NR>1{print $3}' "$TM" | sort | uniq -c | sort -k1,1nr > $O/gc_$m.classcodes.txt
  CSUM=$(awk '{s+=$1} END{print s+0}' $O/gc_$m.classcodes.txt)
  {
    echo "$m query_transcripts (check_st) $NQ"
    echo "$m tmap_rows $ROWS   want == query transcripts"
    echo "$m stats_query_mRNAs $QM   want == query transcripts"
    echo "$m class_code_total $CSUM   want == tmap rows"
    echo "$m multi_exon_'='_in_tmap $EQ_MULTI   check_st chain matches $CHAIN"
    echo "$m query_introns gffcompare $QI   check_st distinct $DI   want equal"
    echo "$m reference_introns gffcompare $RI   check_st distinct $(j reference distinct_introns)   want equal"
    echo "$m intron_level sens/prec gffcompare $IL   recomputed $ILX   want equal"
    echo "$m '=' disagreements: $(wc -l < $O/gc_$m.eq_disagree.txt | tr -d ' ')"
    cat $O/gc_$m.eq_disagree.tmap
    awk -v m=$m '/level:/{print m, $0}' $O/gc_$m.stats
    awk -v m=$m '/level:/{print m, "-R", $0}' $O/gcR_$m.stats
    awk -v m=$m '/Matching|Missed|Novel/{print m, $0}' $O/gc_$m.stats
    awk -v m=$m '{print m, "class", $2, $1}' $O/gc_$m.classcodes.txt
  } | tee -a $O/check.txt
  [ "$ROWS" = "$NQ" ] || fail "$m: tmap has $ROWS rows for $NQ transcripts"
  [ "$QM" = "$NQ" ] || fail "$m: gffcompare saw $QM query mRNAs, the assembly has $NQ"
  [ "$CSUM" = "$ROWS" ] || fail "$m: class codes sum to $CSUM"
  [ "$EQ_MULTI" = "$CHAIN" ] || fail "$m: gffcompare '=' multi-exon $EQ_MULTI != independent chain matches $CHAIN"
  [ "$QI" = "$DI" ] || fail "$m: gffcompare counts $QI query introns, check_st $DI"
  [ "$RI" = "$(j reference distinct_introns)" ] || fail "$m: gffcompare counts $RI reference introns, check_st $(j reference distinct_introns)"
  [ "$IL" = "$ILX" ] || fail "$m: intron level $IL != recomputed $ILX"
done
cat /sys/fs/cgroup/memory.peak > $O/memory-peak.txt 2>/dev/null || echo unreadable > $O/memory-peak.txt
[ "$FAILED" = 0 ] || exit 1
echo "GFFCOMPARE CHECKS PASSED"
