#!/usr/bin/env bash
# Task 01 (vg image, every chip): HG002's chr20 Illumina reads mapped with Giraffe to the HPRC v1.1
# chr20 graph, indexed here with this build of vg, then checked against what the graph and the
# reads must satisfy and against novoalign's linear placement of the same reads.
#   giraffe.sh <threads> [pairs]   pairs > 0 maps only the first that many pairs (a sizing probe)
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
T=${1:?threads}; N=${2:-0}
machine
rec vg_version "$(vg version 2>&1 | head -1)"
M=$(od -An -tu2 -j18 -N2 "$(command -v vg)" | tr -d ' ')
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=? ;; esac
chk "vg binary architecture == host" "$M" "$WANT"

peak(){ # peak <pid>: the process's VmHWM in kB; a high-water mark, so the last read is the peak
  local h=0 x
  while kill -0 "$1" 2>/dev/null; do
    x=$(awk '/^VmHWM/{print $2}' /proc/$1/status 2>/dev/null); [ -n "$x" ] && h=$x
    sleep 1
  done
  echo $h
}
run(){ # run <name> <stdout> <stderr> <cmd...>: time it, record its peak RSS and exit code
  local n=$1 o=$2 e=$3; shift 3
  tic; "$@" > "$o" 2> "$e" & local p=$!; local h; h=$(peak $p); wait $p; local rc=$?; toc "$n"
  rec "${n}_vmhwm_kib" "$h"; chk "$n exit" "$rc" 0
}
# GNU sort takes a buffer size and a temp dir; BusyBox sort (the x86 images) takes neither
if sort --version > /dev/null 2>&1; then SORT="sort -S 40% -T $W"; else SORT=sort; fi

echo "== staged inputs against pins.sha256 =="
grep -E '  (chr20\.d9\.vg|r1\.fq\.gz|r2\.fq\.gz|linear\.tsv\.gz|chr20\.fa)$' pins.sha256 > "$W/in.sha256"
chk "pins.sha256 covers all five inputs" "$(wc -l < "$W/in.sha256" | tr -d ' ')" 5
tic; sha256sum -c "$W/in.sha256"; chk "staged inputs match their pins" "$?" 0; toc sha256

R1=r1.fq.gz; R2=r2.fq.gz
if [ "$N" -gt 0 ]; then   # head on a gzip stream: SIGPIPE under pipefail, so let awk read to the end instead
  for m in 1 2; do gzip -dc r$m.fq.gz | awk -v n=$((4*N)) 'NR<=n' | gzip -1 > "$W/s$m.fq.gz"; done
  R1=$W/s1.fq.gz; R2=$W/s2.fq.gz
  rec probe_pairs "$N"
fi
PAIRS=$(gzip -dc "$R1" | awk 'END{print NR/4}')
rec pairs "$PAIRS"

echo "== the graph =="
vg stats -z -l chr20.d9.vg > "$O/graph.stats.txt" 2>&1
cat "$O/graph.stats.txt"
rec graph_nodes "$(awk '$1=="nodes"{print $2}' "$O/graph.stats.txt")"
rec graph_edges "$(awk '$1=="edges"{print $2}' "$O/graph.stats.txt")"
rec graph_length "$(awk '$1=="length"{print $2}' "$O/graph.stats.txt")"
vg paths -M -x chr20.d9.vg > "$W/paths.txt" 2> "$W/paths.err"
chk "vg paths -M exit" "$?" 0
# the samples the graph was built from (column 3), fragments per sample; HG002 is held out of v1.1
awk -F'\t' 'NR>1{print $3 "\t" $2}' "$W/paths.txt" | sort | uniq -c > "$O/samples.txt"
rec graph_samples "$(wc -l < "$O/samples.txt" | tr -d ' ')"
rec graph_paths "$(grep -vc '^#' "$W/paths.txt")"
rec graph_reference_paths "$(awk -F'\t' '$2=="REFERENCE"{print $3}' "$W/paths.txt" | sort | uniq -c | awk '{printf "%s:%s ", $2, $1}')"
chk "HG002 is not in the graph (held out of HPRC v1.1)" "$(awk '$2 ~ /^HG002$/' "$O/samples.txt" | wc -l | tr -d ' ')" 0

echo "== the identity: the graph's GRCh38 path spells chr20.fa back exactly =="
tic
vg paths -F -Q GRCh38 -x chr20.d9.vg > "$W/ref.fa" 2>> "$W/paths.err"
chk "vg paths -F exit" "$?" 0
rec ref_paths "$(grep '^>' "$W/ref.fa" | tr '\n' ' ')"
chk "one GRCh38 path, unbroken" "$(grep -c '^>' "$W/ref.fa")" 1
REF=$(tail -n +2 chr20.fa | tr -d '\n' | tr acgtn ACGTN | sha256sum | awk '{print $1}')
GP=$(grep -v '^>' "$W/ref.fa" | tr -d '\n' | tr acgtn ACGTN | sha256sum | awk '{print $1}')
chk "GRCh38 path sequence sha256 == chr20.fa sequence sha256" "$GP" "$REF"
toc refpath
rm -f "$W/ref.fa"

echo "== index: GFA, then vg autoindex --workflow giraffe =="
run convert "$W/chr20.gfa" "$O/convert.log" vg convert -f chr20.d9.vg
rec gfa_bytes "$(wc -c < "$W/chr20.gfa" | tr -d ' ')"
run autoindex "$O/autoindex.out" "$O/autoindex.log" vg autoindex --workflow giraffe -g "$W/chr20.gfa" -p "$W/idx" -t "$T"
ls -l "$W"/idx.* | tee "$O/index.ls"
( cd "$W" && sha256sum idx.* ) > "$O/index.sha256"
GBZ=$W/idx.giraffe.gbz; DIST=$W/idx.dist
MIN=$(ls "$W"/idx*.min | head -1); ZIP=$(ls "$W"/idx*.zipcodes 2>/dev/null | head -1)
rm -f "$W/chr20.gfa"

echo "== giraffe =="
run giraffe "$W/aln.gaf" "$O/giraffe.log" vg giraffe -Z "$GBZ" -d "$DIST" -m "$MIN" ${ZIP:+-z "$ZIP"} \
  -f "$R1" -f "$R2" -t "$T" -o gaf
tail -n 20 "$O/giraffe.log"
rec gaf_bytes "$(wc -c < "$W/aln.gaf" | tr -d ' ')"

echo "== checks on the GAF, streamed =="
# Every read written once, mates adjacent; cs:Z consumes exactly the query and target spans it claims.
tic
awk -F'\t' -v OFS='\t' -v odd="$O/odd.gaf" -v tags="$W/gaf.tags" '
  function cslen(cs,   q, t, s, op, n) {   # query and target bases a cs:Z difference string consumes
    q = 0; t = 0
    while (cs != "") {
      op = substr(cs, 1, 1); cs = substr(cs, 2)
      if (op == ":") { match(cs, /^[0-9]+/); n = substr(cs, 1, RLENGTH) + 0; cs = substr(cs, RLENGTH + 1); q += n; t += n }
      else if (op == "*") { cs = substr(cs, 3); q++; t++ }
      else if (op == "+") { match(cs, /^[a-zA-Z]+/); q += RLENGTH; cs = substr(cs, RLENGTH + 1) }
      else if (op == "-") { match(cs, /^[a-zA-Z]+/); t += RLENGTH; cs = substr(cs, RLENGTH + 1) }
      else return "bad"
    }
    return q " " t
  }
  /^@/ { hdr++; next }   # GAF header lines (@HD, @RN)
  { for (i = 13; i <= NF; i++) tag[substr($i, 1, 4)]++ }
  { n++
    if (n % 2 == 1) { prev = $1; pline = $0 } else if ($1 != prev) { unpaired++; if (nodd++ < 20) print pline "\n" $0 > odd }
    if ($6 == "*") { unm++; next }
    map++; if ($12 >= 30) q30++; if ($12 == 60) q60++
    if ($10 == $11 && $11 == $2) perfect++
    cs = ""; for (i = 13; i <= NF; i++) if (substr($i, 1, 5) == "cs:Z:") cs = substr($i, 6)
    if (cs == "") { nocs++; if (nodd++ < 20) print $0 > odd; next }
    split(cslen(cs), L, " ")
    if (L[1] != $4 - $3 || L[2] != $9 - $8) csbad++
    if ($9 > $7 || $8 < 0) spanbad++
  }
  END { printf "gaf_records\t%d\ngaf_unpaired_neighbours\t%d\ngaf_mapped\t%d\ngaf_unmapped\t%d\ngaf_mapq30\t%d\ngaf_mapq60\t%d\ngaf_perfect\t%d\ngaf_no_cs\t%d\ngaf_cs_span_mismatch\t%d\ngaf_target_span_outside_path\t%d\ngaf_header_lines\t%d\n",
          n, unpaired, map, unm, q30, q60, perfect, nocs, csbad, spanbad, hdr
        for (t in tag) printf "%s%s ", t, tag[t] > tags }' "$W/aln.gaf" > "$W/gaf.tsv"
toc gaf_checks
cat "$W/gaf.tsv" >> "$O/results.tsv"; cat "$W/gaf.tsv"
rec gaf_tag_counts "$(cat "$W/gaf.tags")"
g(){ awk -F'\t' -v k="$1" '$1==k{print $2}' "$W/gaf.tsv"; }
chk "GAF records == 2 * pairs (every read written)" "$(g gaf_records)" "$((2*PAIRS))"
chk "mates adjacent in the GAF" "$(g gaf_unpaired_neighbours)" 0
chk "GAF read names unique per pair" "$(awk -F'\t' '/^@/{next} {n++} n%2==1{print $1}' "$W/aln.gaf" | $SORT | uniq -d | wc -l | tr -d ' ')" 0
chk "every mapped record carries cs:Z" "$(g gaf_no_cs)" 0
chk "cs:Z consumes exactly the query and target spans, every record" "$(g gaf_cs_span_mismatch)" 0
chk "target span inside the path, every record" "$(g gaf_target_span_outside_path)" 0
# order is thread-dependent; the content is not: hash the sorted records
tic
rec gaf_sorted_sha256 "$($SORT "$W/aln.gaf" | sha256sum | awk '{print $1}')"
toc gaf_sort_hash

echo "== surject onto GRCh38 chr20, against novoalign's placement of the same reads =="
tic
vg surject -x "$GBZ" -n GRCh38 -G "$W/aln.gaf" -i -t "$T" -s 2> "$O/surject.log" \
  | awk -F'\t' -v OFS='\t' -v flags="$W/surj.flags" -v cnt="$W/surj.cnt" -v nobitf="$O/surj_no_mate_bit.sam" '
    # vg surject writes some paired reads with no 0x40/0x80 mate bit at all (flag 0, 16 or 4). Their
    # mate is unknowable from the SAM, so they keep mate 0, are counted, and never join a linear record.
    /^@/ { next }
    int($2/256)%2 == 0 && int($2/2048)%2 == 0 {
      m = int($2/64)%2 ? 1 : (int($2/128)%2 ? 2 : 0)
      if (!m) { nobit++; print $2 > flags; if (nobit <= 20) print substr($0, 1, 300) > nobitf }
      u = int($2/4)%2; c = $3; sub(/^.*#/, "", c)
      k = 0; if (!u && match($6, /^[0-9]+[SH]/)) k = substr($6, 1, RLENGTH - 1) + 0
      print $1, m, (u ? "*" : c), (u ? 0 : $4), $5, (u ? 0 : $4 - k), "G"
    }
    END { printf "surj_no_mate_bit\t%d\n", nobit > cnt }' > "$W/surj.tsv"
chk "surject exit" "${PIPESTATUS[0]}" 0
cat "$W/surj.cnt" >> "$O/results.tsv"; cat "$W/surj.cnt"
NOBIT=$(awk -F'\t' '$1=="surj_no_mate_bit"{print $2}' "$W/surj.cnt")
rec surj_no_mate_bit_flags "$(sort "$W/surj.flags" 2>/dev/null | uniq -c | awk '{printf "%s:%s ", $2, $1}')"
toc surject
rec surject_records "$(wc -l < "$W/surj.tsv" | tr -d ' ')"
chk "surjected primary records == 2 * pairs" "$(wc -l < "$W/surj.tsv" | tr -d ' ')" "$((2*PAIRS))"
rec surject_sorted_sha256 "$($SORT "$W/surj.tsv" | sha256sum | awk '{print $1}')"
rm -f "$W/aln.gaf"
tic
# name mate contig pos mapq ustart src; both sides sorted together, so a pair of rows is one read.
# ustart (POS less the leading clip) compares where each aligner put the read's first base, so
# a difference in what the two chose to soft-clip is not counted as a disagreement in placement.
{ gzip -dc linear.tsv.gz | awk -v OFS='\t' '{print $0, "L"}'; cat "$W/surj.tsv"; } \
  | $SORT -t "$(printf '\t')" -k1,1 -k2,2n -k7,7 \
  | awk -F'\t' -v un="$O/unjoined.txt" '
    { k = $1 "\t" $2
      if (ps == "G" && !(k == pk && $7 == "L") && nun++ < 50) print pline "\n" $0 > un
      pline = $0
      if (k == pk && ps == "G" && $7 == "L") {
        both++
        if ($3 != "*") lin++
        if (pc != "*") gr++
        if ($3 != "*" && pc != "*") bm++
        if ($3 != "*" && $5 >= 30 && pc != "*" && pq >= 30) {
          conf++; d = $4 - pp; if (d < 0) d = -d
          if ($3 == pc && d <= 5) agree++
          e = $6 - pu; if (e < 0) e = -e
          if ($3 == pc && e <= 5) uagree++
        }
        if ($3 != "*" && $5 >= 30 && pc == "*") lin_only_conf++
        if (pc != "*" && pq >= 30 && $3 == "*") gr_only_conf++
      }
      pk = k; ps = $7; pc = $3; pp = $4; pq = $5; pu = $6 }
    END { printf "joined_reads\t%d\nlinear_mapped\t%d\ngiraffe_mapped\t%d\nboth_mapped\t%d\nboth_mapq30\t%d\nboth_mapq30_pos_within_5bp\t%d\nconfident_pos_concordance\t%.4f\nboth_mapq30_ustart_within_5bp\t%d\nconfident_concordance\t%.4f\nlinear_mapq30_giraffe_unmapped\t%d\ngiraffe_mapq30_linear_unmapped\t%d\n",
            both, lin, gr, bm, conf, agree, (conf ? agree / conf : 0), uagree, (conf ? uagree / conf : 0), lin_only_conf, gr_only_conf }' > "$W/cmp.tsv"
toc compare
cat "$W/cmp.tsv" >> "$O/results.tsv"; cat "$W/cmp.tsv"
# exact accounting: every read joins novoalign's record of it, except the ones surject left mate-less
[ "$N" -gt 0 ] || chk "joined reads == 2 * pairs - surject's mate-less records" \
  "$(awk -F'\t' '$1=="joined_reads"{print $2}' "$W/cmp.tsv")" "$((2*PAIRS - NOBIT))"
C=$(awk -F'\t' '$1=="confident_concordance"{print $2}' "$W/cmp.tsv")
chk "confident placements agree with novoalign (>= 0.98)" "$(awk -v c="$C" 'BEGIN{print (c >= 0.98) ? "yes" : "no"}')" yes
rm -f "$W/surj.tsv"
finish "GIRAFFE"
