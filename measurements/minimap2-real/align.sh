# measurements/minimap2-real: 33x NA12878 PacBio HiFi from chr20 (217,281 reads, 2.16 Gbp)
# aligned with -ax map-hifi against the WHOLE GRCh38 no-alt analysis set, so every read competes
# against the full genome as it does in a real run, and the index is built from all 3.1 Gbp.
# Runs in the recipe's minimap2 image (GNU userland on arm64, BusyBox on bioconda's x86), so the
# glue is portable: no join/numfmt/perl, no date +%N, no awk and(), no early-exit readers.
set -uo pipefail
cd /tmp
THREADS=16
REF=GRCh38_no_alt_analysis_set.fa
READS=hifi.chr20.fq.gz
fail(){ echo "FAIL: $*"; exit 1; }
: > results.tsv
rec(){ printf '%s\t%s\n' "$1" "$2" >> results.tsv; echo "$1	$2"; }

echo "== the box =="
rec uname_m "$(uname -m)"
rec nproc "$(nproc)"
MODEL=$(awk -F': *' '/^model name/{m=$2} /^CPU part/{p=$2} END{if(m!="")print m; else print "arm CPU part " p}' /proc/cpuinfo)
rec cpu_model "$MODEL"
TPC=$(awk -F, '{n=0; for(i=1;i<=NF;i++){ if($i~/-/){split($i,a,"-"); n+=a[2]-a[1]+1} else n++ } print n}' \
  /sys/devices/system/cpu/cpu0/topology/thread_siblings_list)
rec threads_per_core "$TPC"
lscpu > cpu.txt 2>/dev/null || cat /proc/cpuinfo > cpu.txt
minimap2 --version > ver.txt 2>&1
rec minimap2_version "$(awk 'NR==1' ver.txt)"

# ---- 1 Hz cgroup sampler (the measurements/sizing-ratio instrument) ----
CG=/sys/fs/cgroup
sample(){ : > samp.tsv
  while :; do
    m=$(cat $CG/memory.current 2>/dev/null || echo 0)
    u=$(awk '/^usage_usec/{print $2}' $CG/cpu.stat 2>/dev/null || echo 0)
    printf '%s\t%s\t%s\n' "$(date +%s)" "$m" "$u" >> samp.tsv
    sleep 1
  done; }
sample & SP=$!
trap 'kill $SP 2>/dev/null || true' EXIT

echo "== minimap2 -ax map-hifi -t $THREADS --MD, whole GRCh38 no-alt =="
T0=$(date +%s)
minimap2 -ax map-hifi -t "$THREADS" --MD "$REF" "$READS" > aln.sam 2> minimap2.log
RC=$?
T1=$(date +%s)
kill $SP 2>/dev/null || true
tail -3 minimap2.log
test "$RC" = 0 || fail "minimap2 exit $RC"
rec minimap2_wall_s "$(( T1 - T0 ))"
# minimap2's own accounting: the index build, then the whole-run real/CPU/peak-RSS line.
rec index_build_s "$(awk '/loaded\/built the index/{s=$0; sub(/^\[M::main::/,"",s); sub(/\*.*/,"",s); print s; exit}' minimap2.log)"
rec mm2_real_cpu_rss "$(awk '/Real time:/{sub(/^\[M::main\] /,""); print}' minimap2.log)"
rec peak_cgroup_gib "$(awk -F'\t' '{if($2+0>m)m=$2+0} END{printf "%.2f", m/1073741824}' samp.tsv)"
rec avg_cores "$(awk -F'\t' 'NR==1{u0=$3;t0=$1} END{if($1>t0) printf "%.2f", ($3-u0)/1e6/($1-t0)}' samp.tsv)"

echo "== the alignment, from the SAM itself =="
T2=$(date +%s)
grep -v '^@' aln.sam | md5sum | awk '{print $1}' > body.md5
grep '^@' aln.sam > hdr.txt
rec sam_body_md5 "$(cat body.md5)"
rec sam_header_lines "$(wc -l < hdr.txt)"
rec sam_sq_lines "$(grep -c '^@SQ' hdr.txt)"
# Flag bits by arithmetic (no and() in BusyBox awk). Every counter goes through %.0f: 2.16 Gbp of
# aligned bases overflows a 32-bit %d.
awk -F'\t' '
  FNR==NR { pc[$1]=$2; ps[$1]=$3; pe[$1]=$4; pq[$1]=$5; next }
  /^@/ { next }
  {
    f=$2; recs++
    if (int(f/256)%2)  { sec++; next }
    if (int(f/2048)%2) { sup++; next }
    prim++
    if (seen[$1]++) dupprim++
    if ($1 in pc) intable++
    if ($3=="*") { unmapped++; next }
    mapped++
    if ($3=="chr20") onchr++
    if ($5>=30) q30++
    bl=0; rl=0; n=""
    for (i=1;i<=length($6);i++) { ch=substr($6,i,1)
      if (ch ~ /[0-9]/) n=n ch
      else { v=n+0; n=""
        if (ch=="M"||ch=="I"||ch=="D"||ch=="="||ch=="X") bl+=v
        if (ch=="M"||ch=="D"||ch=="="||ch=="X"||ch=="N") rl+=v } }
    nm=-1
    for (i=12;i<=NF;i++) if ($i ~ /^NM:i:/) nm=substr($i,6)+0
    if (nm>=0 && bl>0) { totbl+=bl; totnm+=nm }
    # Placement vs the depositors pbmm2 (a minimap2 wrapper, CCS preset, same no-alt reference),
    # only where BOTH are confident: MAPQ>=30 on each side.
    if (($1 in pq) && pq[$1]>=30 && $5>=30) {
      both++
      s=$4; e=$4+rl-1
      if ($3==pc[$1] && s<=pe[$1] && ps[$1]<=e) agree++
      if ($3==pc[$1] && s==ps[$1]) samestart++
    }
  }
  END {
    printf "sam_records\t%.0f\nsecondary\t%.0f\nsupplementary\t%.0f\n", recs, sec, sup
    printf "primary_records\t%.0f\nduplicate_primaries\t%.0f\nprimaries_in_pbmm2_table\t%.0f\n", prim, dupprim, intable
    printf "mapped\t%.0f\nunmapped\t%.0f\non_chr20\t%.0f\nmapq30\t%.0f\n", mapped, unmapped, onchr, q30
    printf "pct_mapped\t%.3f\npct_mapped_on_chr20\t%.3f\n", 100*mapped/prim, 100*onchr/mapped
    printf "aligned_bases\t%.0f\nmean_identity\t%.4f\n", totbl, 100*(1-totnm/totbl)
    printf "both_confident\t%.0f\nconfident_overlap_pbmm2\t%.0f\nconfident_same_start\t%.0f\n", both, agree, samestart
    printf "confident_concordance\t%.5f\n", (both>0 ? agree/both : 0)
  }' pbmm2.chr20.tsv aln.sam > st.tsv || fail "stats awk"
cat st.tsv; cat st.tsv >> results.tsv
rec stats_wall_s "$(( $(date +%s) - T2 ))"
g(){ awk -F'\t' -v k="$1" '$1==k{print $2}' st.tsv; }

echo "== checks =="
ok=1
chk(){ if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1"; ok=0; fi; }
# Conservation: minimap2 must account for every input read exactly once, mapped or not.
chk "primary_records == 217281"            '[ "$(g primary_records)" = 217281 ]'
chk "no read has two primaries"            '[ "$(g duplicate_primaries)" = 0 ]'
# Every read is one of the depositors' chr20 primaries -- the read set is what it claims to be.
chk "every read in the pbmm2 table"        '[ "$(g primaries_in_pbmm2_table)" = 217281 ]'
chk "header lists the 195 no-alt sequences" '[ "$(grep -c "^@SQ" hdr.txt)" = 195 ]'
chk "pct_mapped >= 95"                     'awk -v v="$(g pct_mapped)" "BEGIN{exit !(v>=95)}"'
chk "mean identity >= 98"                  'awk -v v="$(g mean_identity)" "BEGIN{exit !(v>=98)}"'
# Two minimap2 engines on the same reference, where both are sure: they must place the read at the
# same locus. Overlap, not start, because the ends of a 10 kb read clip differently by preset.
chk "confident placements agree >= 0.99"   'awk -v v="$(g confident_concordance)" "BEGIN{exit !(v>=0.99)}"'
test "$ok" = 1 || fail "a check failed"
echo "MINIMAP2 REAL OK"
