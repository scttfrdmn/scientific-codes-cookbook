# measurements/minimap2-real: the whole NA12878 PacBio HiFi deposit (~30x) aligned with
# -ax map-hifi against GRCh38 no-alt, one index build, then the 25 read chunks in order.
# Each chunk's SAM is written, summarised, hashed and deleted before the next, so the staging
# tmpfs holds the reads plus one chunk's SAM. minimap2_wall is the sum of the aligner's own runs.
# Portable glue: GNU userland on arm64, BusyBox on bioconda's x86 image.
set -uo pipefail
cd /tmp
THREADS=${THREADS:-32}
REF=GRCh38_no_alt_analysis_set.fa
fail(){ echo "FAIL: $*"; exit 1; }
: > results.tsv
rec(){ printf '%s\t%s\n' "$1" "$2" >> results.tsv; echo "$1	$2"; }

echo "== the box =="
rec uname_m "$(uname -m)"
rec nproc "$(nproc)"
rec cpu_model "$(awk -F': *' '/^model name/{m=$2} /^CPU part/{p=$2} END{if(m!="")print m; else print "arm CPU part " p}' /proc/cpuinfo)"
rec threads_per_core "$(awk -F, '{n=0; for(i=1;i<=NF;i++){ if($i~/-/){split($i,a,"-"); n+=a[2]-a[1]+1} else n++ } print n}' \
  /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)"
rec threads "$THREADS"
lscpu > cpu.txt 2>/dev/null || cat /proc/cpuinfo > cpu.txt
minimap2 --version > ver.txt 2>&1
rec minimap2_version "$(awk 'NR==1' ver.txt)"

# 1 Hz: container memory, container CPU, and the staging tmpfs high-water mark.
CG=/sys/fs/cgroup
sample(){ : > samp.tsv
  while :; do
    m=$(cat $CG/memory.current 2>/dev/null || echo 0)
    u=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null || echo 0)
    d=$(df -k /tmp | awk 'NR==2{print $3}')
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$m" "$u" "$d" >> samp.tsv
    sleep 1
  done; }
sample & SP=$!
trap 'kill $SP 2>/dev/null || true' EXIT

echo "== index: minimap2 -x map-hifi -d, whole GRCh38 no-alt =="
T0=$(date +%s)
minimap2 -x map-hifi -t "$THREADS" -d ref.mmi "$REF" 2> index.log || { cat index.log; fail "index build"; }
rec index_wall_s "$(( $(date +%s) - T0 ))"

: > chunks.tsv
: > minimap2.log
MMW=0
grep -v '^#' wg/chunks.order > order.txt
while read -r C; do
  T1=$(date +%s)
  minimap2 -ax map-hifi -t "$THREADS" --MD ref.mmi "wg/$C.fq.gz" > c.sam 2> c.log
  RC=$?
  T2=$(date +%s)
  echo "== $C ==" >> minimap2.log; cat c.log >> minimap2.log
  test "$RC" = 0 || { tail c.log; fail "minimap2 exit $RC on $C"; }
  W=$(( T2 - T1 )); MMW=$(( MMW + W ))
  test -s hdr.sam || grep '^@' c.sam > hdr.sam
  MD5=$(grep -v '^@' c.sam | md5sum | awk '{print $1}')
  RSS=$(awk '/Real time:/{for(i=1;i<=NF;i++) if($i=="RSS:") print $(i+1)}' c.log)
  # Flag bits by arithmetic (no and() in BusyBox awk); counters via %.0f (32-bit %d overflows).
  # Records placed on chr20 are appended to chr20.body for the GIAB truth-support check.
  awk -F'\t' -v C="$C" -v W="$W" -v MD5="$MD5" -v RSS="$RSS" '
    FNR==NR { pc[$1]=$2; ps[$1]=$3; pe[$1]=$4; pq[$1]=$5; next }
    /^@/ { next }
    {
      f=$2; recs++
      if ($3=="chr20") print >> "chr20.body"
      if (int(f/256)%2)  { sec++; next }
      if (int(f/2048)%2) { sup++; next }
      prim++
      if (seen[$1]++) dup++
      if ($1 in pc) intab++
      if ($3=="*") { unm++; next }
      mapped++
      if ($5>=30) q30++
      bl=0; rl=0; n=""
      for (i=1;i<=length($6);i++) { ch=substr($6,i,1)
        if (ch ~ /[0-9]/) n=n ch
        else { v=n+0; n=""
          if (ch=="M"||ch=="I"||ch=="D"||ch=="="||ch=="X") bl+=v
          if (ch=="M"||ch=="D"||ch=="="||ch=="X"||ch=="N") rl+=v } }
      nm=-1
      for (i=12;i<=NF;i++) if ($i ~ /^NM:i:/) nm=substr($i,6)+0
      if (nm>=0 && bl>0) { tbl+=bl; tnm+=nm }
      if (($1 in pq) && pq[$1]>=30 && $5>=30) {
        both++; s=$4; e=$4+rl-1
        if ($3==pc[$1] && s<=pe[$1] && ps[$1]<=e) agree++
      }
    }
    END { printf "%s\t%s\t%s\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f\t%s\n",
            C, W, RSS, recs, sec, sup, prim, dup, intab, mapped, unm, q30, tbl, tnm, both, agree, MD5 }
  ' "wg/$C.pbmm2.tsv" c.sam >> chunks.tsv || fail "stats on $C"
  rm -f c.sam
  awk -F'\t' -v C="$C" '$1==C' chunks.tsv
done < order.txt
kill $SP 2>/dev/null || true
cat hdr.sam chr20.body > chr20.sam; rm -f chr20.body
rec minimap2_wall_s "$MMW"
rec peak_cgroup_gib "$(awk -F'\t' '{if($2+0>m)m=$2+0} END{printf "%.2f", m/1073741824}' samp.tsv)"
rec peak_tmpfs_gib "$(awk -F'\t' '{if($4+0>m)m=$4+0} END{printf "%.2f", m/1048576}' samp.tsv)"
rec avg_cores "$(awk -F'\t' 'NR==1{u0=$3;t0=$1} END{if($1>t0) printf "%.2f", ($3-u0)/1e6/($1-t0)}' samp.tsv)"
rec peak_mm2_rss_gb "$(awk -F'\t' '{if($3+0>m)m=$3+0} END{print m}' chunks.tsv)"

# Totals. Columns: 1 chunk 2 wall 3 rss 4 recs 5 sec 6 sup 7 prim 8 dup 9 intab 10 mapped 11 unm
# 12 q30 13 aligned_bases 14 nm 15 both_confident 16 agree 17 body_md5
awk -F'\t' '{for(i=4;i<=16;i++) s[i]+=$i}
  END{ printf "sam_records\t%.0f\nsecondary\t%.0f\nsupplementary\t%.0f\nprimary_records\t%.0f\n", s[4],s[5],s[6],s[7]
       printf "duplicate_primaries\t%.0f\nprimaries_in_pbmm2_table\t%.0f\nmapped\t%.0f\nunmapped\t%.0f\nmapq30\t%.0f\n", s[8],s[9],s[10],s[11],s[12]
       printf "pct_mapped\t%.3f\naligned_bases\t%.0f\nmean_identity\t%.4f\n", 100*s[10]/s[7], s[13], 100*(1-s[14]/s[13])
       printf "both_confident\t%.0f\nconfident_overlap_pbmm2\t%.0f\nconfident_concordance\t%.5f\n", s[15], s[16], s[16]/s[15] }' \
  chunks.tsv > st.tsv
cat st.tsv; cat st.tsv >> results.tsv
awk -F'\t' '{print $17"  "$1}' chunks.tsv > body.md5
rec all_chunks_md5 "$(md5sum < body.md5 | awk '{print $1}')"
INREADS=$(awk -F'\t' '{r+=$2} END{printf "%.0f", r}' wg/counts.tsv)
INBASES=$(awk -F'\t' '{b+=$3} END{printf "%.0f", b}' wg/counts.tsv)
rec input_reads "$INREADS"
rec input_bases "$INBASES"
rec chr20_sam_records "$(grep -vc '^@' chr20.sam)"
g(){ awk -F'\t' -v k="$1" '$1==k{print $2}' st.tsv; }
BADCHUNK=$(awk -F'\t' 'NR==FNR{n[$1]=$2; next} n[$1]!=$7{bad++} END{print bad+0}' wg/counts.tsv chunks.tsv)

echo "== checks =="
ok=1
chk(){ if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1"; ok=0; fi; }
chk "25 chunks aligned"                       '[ "$(wc -l < chunks.tsv)" = 25 ]'
# Conservation, per chunk and in total: every input read accounted for exactly once.
chk "per chunk: primaries == input reads"     '[ "$BADCHUNK" = 0 ]'
chk "primary_records == input reads"          '[ "$(g primary_records)" = "$INREADS" ]'
chk "no read has two primaries"               '[ "$(g duplicate_primaries)" = 0 ]'
chk "every read is in the pbmm2 table"        '[ "$(g primaries_in_pbmm2_table)" = "$INREADS" ]'
chk "header lists the 195 no-alt sequences"   '[ "$(grep -c "^@SQ" hdr.sam)" = 195 ]'
chk "pct_mapped >= 95"                        'awk -v v="$(g pct_mapped)" "BEGIN{exit !(v>=95)}"'
chk "mean identity >= 98"                     'awk -v v="$(g mean_identity)" "BEGIN{exit !(v>=98)}"'
chk "confident placements agree >= 0.99"      'awk -v v="$(g confident_concordance)" "BEGIN{exit !(v>=0.99)}"'
test "$ok" = 1 || fail "a check failed"
echo "MINIMAP2 GENOME OK"
