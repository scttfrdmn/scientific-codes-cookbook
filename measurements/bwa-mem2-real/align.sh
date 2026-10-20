#!/usr/bin/env bash
# One task per aligner per chip: HG00096's whole low-coverage genome (every paired run in the
# 1000 Genomes sequence.index) aligned to the full GRCh38 analysis set, one invocation per run with
# its own read group, as 1000 Genomes aligned it.
#   align.sh bwa <threads>        task 01: bwa mem; stages out its per-record core fields
#   align.sh bwa-mem2 <threads>   task 02: bwa-mem2 mem; then compares itself to task 01, run by run
# The aligner writes plain SAM to the container disk -- no `| gzip` on the timed path, because a
# single-threaded gzip downstream of 16 alignment threads times the compressor, not the aligner.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
TOOL=${1:?bwa or bwa-mem2}; T=${2:?threads}
REF=GRCh38_full_analysis_set_plus_decoy_hla.fa
TAB=$(printf '\t')
machine
rec tool "$TOOL"
rec threads "$T"
M=$(od -An -tu2 -j18 -N2 "$(command -v "$TOOL")" | tr -d ' ')
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=? ;; esac
chk "$TOOL binary architecture == host" "$M" "$WANT"
if [ "$TOOL" = bwa ]; then
  bwa > "$W/ver.txt" 2>&1
  rec version "$(awk '/^Version/{print $2}' "$W/ver.txt")"
  P=/tmp/$REF
else
  rec version "$(bwa-mem2 version 2>/dev/null | awk 'END{print $NF}')"
  ls "$(dirname "$(command -v bwa-mem2)")" | grep '^bwa-mem2' | tr '\n' ' ' > "$W/kernels.txt"
  rec kernels_shipped "$(cat "$W/kernels.txt")"
  P=$W/$REF
fi

echo "== reads against the md5s 1000 Genomes publishes for them =="
awk -F'\t' '!/^#/{print $2 "  " $1 "_1.filt.fastq.gz"; print $3 "  " $1 "_2.filt.fastq.gz"}' sample.tsv > "$W/reads.md5"
chk "sample.tsv lists the three low-coverage runs" "$(awk '!/^#/' sample.tsv | wc -l | tr -d ' ')" 3
tic; md5sum -c "$W/reads.md5"; chk "reads match the published md5s" "$?" 0; toc md5

echo "== reference and index: hashed here, compared across chips by collect.sh =="
tic
if [ "$TOOL" = bwa ]; then
  for e in amb ann bwt pac sa alt; do rec "sha256_$REF.$e" "$(sha < "$REF.$e")"; done
else
  rec sha256_mem2-index.tar "$(sha < mem2-index.tar)"
  rec "sha256_$REF.alt" "$(sha < "$REF.alt")"
  tar -xf mem2-index.tar -C "$W"; chk "untar the bwa-mem2 index" "$?" 0
  ln -sf "/tmp/$REF.alt" "$W/$REF.alt"
  for e in 0123 bwt.2bit.64 amb ann pac; do rec "sha256_$REF.$e" "$(sha < "$W/$REF.$e")"; done
fi
toc hash_index

TOT_WALL=0; TOT_PAIRS=0; TOT_REC=0; HWM=0
for RUN in $(awk -F'\t' '!/^#/{print $1}' sample.tsv); do
  PAIRS=$(awk -F'\t' -v r="$RUN" '$1==r{print $4}' sample.tsv)
  SAM=$W/$RUN.sam
  echo "== $TOOL mem -t $T: $RUN ($PAIRS pairs) =="
  U0=$(cpu_usec); tic
  "$TOOL" mem -t "$T" -R "@RG\tID:$RUN\tSM:HG00096\tPL:ILLUMINA" "$P" \
      "${RUN}_1.filt.fastq.gz" "${RUN}_2.filt.fastq.gz" > "$SAM" 2> "$O/$RUN.$TOOL.log" &
  PID=$!; H=$(peak $PID); wait $PID; RC=$?
  toc "align_$RUN"; WALL=$d; U1=$(cpu_usec)
  chk "$RUN $TOOL exit" "$RC" 0
  rec "${RUN}_wall_s" "$WALL"
  rec "${RUN}_vmhwm_kib" "$H"
  rec "${RUN}_avg_cores" "$(awk -v a="$U0" -v b="$U1" -v w="$WALL" 'BEGIN{printf "%.2f", (b-a)/1e6/w}')"
  [ "$H" -gt "$HWM" ] && HWM=$H
  TOT_WALL=$((TOT_WALL + WALL)); TOT_PAIRS=$((TOT_PAIRS + PAIRS))
  # bwa: "read 3171 ALT contigs"; bwa-mem2: "Read 3171 ALT contigs"
  rec "${RUN}_alt_contigs" "$(awk 'tolower($0) ~ /read [0-9]+ alt contigs/{for(i=1;i<=NF;i++) if(tolower($i)=="read") v=$(i+1)} END{print v}' "$O/$RUN.$TOOL.log")"
  chk "$RUN ALT contigs read (the analysis set, used ALT-aware)" "$(awk -F'\t' -v k="${RUN}_alt_contigs" '$1==k{print $2}' "$O/results.tsv")" 3171
  if [ "$TOOL" = bwa-mem2 ]; then
    rec "${RUN}_kernel" "$(awk '/^Executing in/{print $3}' "$O/$RUN.$TOOL.log")"
    rec "${RUN}_launched" "$(awk -F'"' '/^Launching executable/{print $2}' "$O/$RUN.$TOOL.log")"
    rec "${RUN}_sam_write_io_s" "$(awk -F'avg: ' '/Writing IO time \(SAM\)/{split($2,a,","); print a[1]}' "$O/$RUN.$TOOL.log")"
  fi

  echo "== $RUN: hash, count and cut the SAM, four readers at once =="
  tic
  RED=$X/$RUN.tsv; [ "$TOOL" = bwa-mem2 ] && RED=$W/$RUN.red.tsv
  ( grep -v '^@' "$SAM" | sha > "$W/$RUN.body.sha" ) &
  # the same body without MQ:i (mate MAPQ), which bwa added after 0.7.17 (lh3/bwa d8dd308a, 2021)
  # and bwa-mem2, which tracks 0.7.17, never writes -- so this is the like-with-like hash
  ( grep -v '^@' "$SAM" | sed "s/${TAB}MQ:i:[0-9]*//" | sha > "$W/$RUN.nomq.sha" ) &
  ( grep -v '^@' "$SAM" | grep -c "${TAB}MQ:i:" > "$W/$RUN.mq.n" ) &
  ( awk '/^@/{print; next}{exit}' "$SAM" > "$O/$RUN.$TOOL.header.txt" ) &
  ( grep -v '^@' "$SAM" | cut -f2 | awk '{c[$1]++} END{for(k in c) print k "\t" c[k]}' > "$W/$RUN.flags" ) &
  ( grep -v '^@' "$SAM" | cut -f1-9,12- > "$RED" ) &
  wait
  toc "post_$RUN"
  rec "${RUN}_body_sha256" "$(cat "$W/$RUN.body.sha")"
  rec "${RUN}_body_nomq_sha256" "$(cat "$W/$RUN.nomq.sha")"
  rec "${RUN}_records_with_mq" "$(cat "$W/$RUN.mq.n")"
  rec "${RUN}_sq_sha256" "$(grep '^@SQ' "$O/$RUN.$TOOL.header.txt" | sha)"
  rec "${RUN}_sam_bytes" "$(wc -c < "$SAM" | tr -d ' ')"
  # flag bits by arithmetic: mawk and BusyBox awk have no and()
  awk -F'\t' '{f=$1; c=$2; n+=c
      sec=int(f/256)%2; sup=int(f/2048)%2; unm=int(f/4)%2; r1=int(f/64)%2; r2=int(f/128)%2; pp=int(f/2)%2
      if (sec) s+=c; else if (sup) u+=c; else { p+=c; if (r1) p1+=c; if (r2) p2+=c; if (!unm) m+=c; if (pp) q+=c } }
      END{printf "records\t%d\nprimary\t%d\nprimary_r1\t%d\nprimary_r2\t%d\nprimary_mapped\t%d\nproper_pair\t%d\nsecondary\t%d\nsupplementary\t%d\n", n,p,p1,p2,m,q,s,u}' \
      "$W/$RUN.flags" > "$W/$RUN.counts"
  while IFS="$(printf '\t')" read -r k v; do rec "${RUN}_$k" "$v"; done < "$W/$RUN.counts"
  g(){ awk -F'\t' -v k="${RUN}_$1" '$1==k{print $2}' "$O/results.tsv"; }
  TOT_REC=$((TOT_REC + $(g records)))
  # every read the publisher counted comes out exactly once as a primary record, and nothing else does
  chk "$RUN primary R1 records == published READ_COUNT" "$(g primary_r1)" "$PAIRS"
  chk "$RUN primary R2 records == published READ_COUNT" "$(g primary_r2)" "$PAIRS"
  chk "$RUN primary records == 2 x READ_COUNT" "$(g primary)" "$((2 * PAIRS))"
  chk "$RUN records == primary + supplementary + secondary" "$(g records)" "$(( $(g primary) + $(g supplementary) + $(g secondary) ))"

  if [ "$TOOL" = bwa-mem2 ]; then
    bget(){ awk -F'\t' -v k="${RUN}_$1" '$1==k{print $2}' /tmp/bwa-results.tsv; }
    # the documented claim: output identical to bwa mem -- every field and tag of every record,
    # SEQ and QUAL included, once bwa's post-0.7.17 MQ tag is set aside
    chk "$RUN bwa-mem2 SAM body == bwa's without MQ, sha256" "$(g body_nomq_sha256)" "$(bget body_nomq_sha256)"
    chk "$RUN bwa-mem2 writes no MQ tag" "$(g records_with_mq)" 0
    chk "$RUN bwa-mem2 records == bwa's" "$(g records)" "$(bget records)"
    chk "$RUN @SQ dictionary == bwa's" "$(g sq_sha256)" "$(bget sq_sha256)"
    if [ "$(g body_nomq_sha256)" != "$(bget body_nomq_sha256)" ]; then
      echo "== $RUN differs from bwa: which fields =="
      # line by line (both aligners emit in input order); counters only, so no hash table grows
      awk -F'\t' -v A="/tmp/bwa-red/$RUN.tsv" -v B="$RED" -v S="$O/$RUN.diff-sample.txt" 'BEGIN{
        split("qname flag rname pos mapq cigar rnext pnext tlen", nm, " ")
        while ((getline a < A) > 0) {
          if ((getline b < B) <= 0) { onlya++; continue }
          n++; if (a == b) continue
          dif++; na = split(a, x, "\t"); nb = split(b, y, "\t"); core = 0
          for (i = 1; i <= 9; i++) if (x[i] != y[i]) { fd[i]++; core = 1 }
          if (core) { cored++; if (x[3] != y[3] || x[4] != y[4]) posd++ } else tagonly++
          split("", ta); split("", tb)
          for (i = 10; i <= na; i++) ta[substr(x[i], 1, 2)] = x[i]
          for (i = 10; i <= nb; i++) tb[substr(y[i], 1, 2)] = y[i]
          for (t in ta) if (ta[t] != tb[t]) td[t]++
          for (t in tb) if (!(t in ta)) td[t]++
          if (dif <= 200) print "bwa\t" a "\nmem2\t" b > S
        }
        while ((getline b < B) > 0) onlyb++
        printf "lines_compared\t%d\nlines_differ\t%d\ncore_fields_differ\t%d\nrname_or_pos_differ\t%d\ntags_only_differ\t%d\nonly_in_bwa\t%d\nonly_in_mem2\t%d\n", n, dif, cored, posd, tagonly, onlya, onlyb
        for (i = 1; i <= 9; i++) if (fd[i]) printf "field_%s_differs\t%d\n", nm[i], fd[i]
        for (t in td) printf "tag_%s_differs\t%d\n", t, td[t]
      }' > "$W/$RUN.diff"
      while IFS="$(printf '\t')" read -r k v; do rec "${RUN}_vs_bwa_$k" "$v"; done < "$W/$RUN.diff"
    else
      rec "${RUN}_vs_bwa_lines_differ" 0
    fi
    rm -f "$RED"
  fi
  rm -f "$SAM"
done
rec total_pairs "$TOT_PAIRS"
rec total_records "$TOT_REC"
rec total_align_wall_s "$TOT_WALL"
rec max_vmhwm_kib "$HWM"
rec pairs_per_s "$(awk -v p="$TOT_PAIRS" -v w="$TOT_WALL" 'BEGIN{printf "%.0f", p/w}')"
finish "$TOOL"
