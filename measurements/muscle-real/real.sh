#!/usr/bin/env bash
# muscle-real (MUSCLE image): align the whole PF01255.25 Pfam seed -- the bytes mafft-real aligned --
# with each step in STEPS, then check every alignment against its own input, against Pfam's curated
# seed alignment, and against mafft's reproducible L-INS-i alignment of the same bytes.
#
#   bash real.sh <unaligned.fa> <ref.fa> <mafft_linsi.fa> <step> [<step> ...]
#
# A step is label:mode:threads, mode one of super5 | align, e.g. s5t1:super5:1 align:align:8.
# A label ending in -rep is a repeat of the label before it: the two are compared byte for byte, and
# asserted identical when the label is in ASSERT_SAME (space-separated, env), reported otherwise.
#
# Writes everything to /tmp/out/. Runs on the aarch.bio image and on bioconda's x86 one (BusyBox:
# no python, no date +%N), so the glue is POSIX awk -- the scorer is mafft-real's, verbatim.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
IN=$1 REF=$2 MAFFT=$3; shift 3
ASSERT_SAME=${ASSERT_SAME:-}
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-48s %-26s %s\n' "$1" "$2" "$3" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }
peak() { awk '{printf "%.2f", $1/1073741824}' /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA; }

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  b=$(command -v muscle)
  echo "muscle $b links: $( (ldd "$b" 2>&1 || echo none) | awk '{printf "%s ", $1}')"
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "muscle_version $(muscle -version 2>&1 | head -n 1)"
} > "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

# "name<TAB>UPPERCASE residues" per record, sorted by name: muscle writes rows in guide-tree order,
# not input order, so content is compared as a set keyed by name (names checked unique below).
fa2tsv() { awk '/^>/{if(n!="")print n"\t"s; n=substr($1,2); s=""; next} {g=$0; gsub(/[-.]/,"",g); s=s toupper(g)} END{if(n!="")print n"\t"s}' "$1" | LC_ALL=C sort; }
same() { awk 'NR==FNR{a[FNR]=$0; n=FNR; next} {if(a[FNR]!=$0)bad++; m=FNR} END{print (n>0 && bad==0 && n==m)?1:0}' "$1" "$2"; }
lens() { awk '/^>/{if(h)print l; h=1; l=0; next} {l+=length($0)} END{if(h)print l}' "$1" | awk 'NR==1{mn=$1} {if($1<mn)mn=$1; if($1>mx)mx=$1} END{print mn+0, mx+0}'; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

# mafft-real's scorer, verbatim: SP = fraction of the reference's same-column residue pairs the test
# alignment also puts in one column; TC = fraction of reference columns (>= 2 residues) reproduced
# whole. Integer counts, so exact. Name-keyed, so row order does not matter.
cat > score.awk <<'AWK'
FNR==1 { f++ }
/^>/ { name=substr($1,2); if (f==1) names[++nn]=name; next }
{ if (f==1) r[name]=r[name] $0; else t[name]=t[name] $0 }
END {
  for (i=1; i<=nn; i++) {
    nm=names[i]; s=r[nm]; k=0; L=length(s)
    for (j=1; j<=L; j++) { c=substr(s,j,1); if (c ~ /[A-Za-z]/) { k++; if (c ~ /[A-Z]/) rc[nm SUBSEP k]=j } }
    s=t[nm]; k=0; L=length(s)
    for (j=1; j<=L; j++) { c=substr(s,j,1); if (c ~ /[A-Za-z]/) { k++; tc[nm SUBSEP k]=j } }
  }
  for (key in rc) { if (!(key in tc)) { miss++; continue } col=rc[key]; n[col]++; cnt[col SUBSEP tc[key]]++ }
  for (key in cnt) { m=cnt[key]; agree+=m*(m-1)/2; split(key, p, SUBSEP); d[p[1]]++ }
  for (col in n) { m=n[col]; total+=m*(m-1)/2; if (m>=2) { cols++; if (d[col]==1) whole++ } }
  printf "pairs_reproduced %d\npairs_reference %d\nsp %.6f\ncolumns_whole %d\ncolumns_reference %d\ntc %.6f\nresidues_unmapped %d\n", agree, total, agree/total, whole, cols, whole/cols, miss+0
}
AWK

fa2tsv "$IN" > in.tsv
NIN=$(awk 'END{print NR}' in.tsv)
RIN=$(awk -F'\t' '{n+=length($2)} END{printf "%.0f", n}' in.tsv)
check "$(same in.tsv <(fa2tsv "$REF"))" "input == Pfam seed with gaps removed" "every record" "$NIN records, $RIN residues"
awk -F'\t' '{print $1}' in.tsv | uniq -d > dup.txt
check "$([ ! -s dup.txt ] && echo 1)" "record names unique" "0 duplicates" "$(awk 'END{print NR}' dup.txt) duplicates"
check "$(same in.tsv <(fa2tsv "$MAFFT"))" "mafft L-INS-i == input, gaps removed" "every record" "$(sha "$MAFFT" | cut -c1-16)"
awk -f score.awk "$REF" "$REF" > "$O/score-ref-vs-ref.txt"
check "$(awk '/^sp /{a=$2} /^tc /{b=$2} END{print (a=="1.000000" && b=="1.000000")?1:0}' "$O/score-ref-vs-ref.txt")" "scorer control: Pfam vs itself" "SP = TC = 1 exactly" "$(tr '\n' ' ' < "$O/score-ref-vs-ref.txt" | cut -c1-80)"
awk -f score.awk "$REF" "$MAFFT" > "$O/score-mafft-linsi.txt"
echo "mafft-linsi vs Pfam: $(tr '\n' ' ' < "$O/score-mafft-linsi.txt")" | tee -a "$O/summary.txt"

PREV=""
for step in "$@"; do
  IFS=: read -r lab mode T <<<"$step"
  echo "== $lab: muscle -$mode -threads $T =="
  u0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
  t0=$(date +%s)
  # progress to command.log every 5 min, so a killed step still says how far it got
  ( while sleep 300 </dev/null >/dev/null 2>&1; do echo "progress $lab $(( $(date +%s) - t0 )) s: $(tr '\r' '\n' < "$O/$lab.log" 2>/dev/null | awk 'NF{l=$0} END{print l}')"; done ) &
  PPID_=$!
  muscle -"$mode" "$IN" -output "$lab.fa" -threads "$T" 2> "$O/$lab.log"
  rc=$?
  t1=$(date +%s)
  kill $PPID_ 2>/dev/null; wait $PPID_ 2>/dev/null
  u1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
  busy=$(awk -v a="$u0" -v b="$u1" -v s=$((t1-t0)) 'BEGIN{printf "%.2f", (s>0 && b!="")?(b-a)/1e6/s:0}')
  echo "$lab: muscle -$mode -threads $T exit $rc in $((t1-t0)) s, $busy cores busy, cgroup memory.peak so far $(peak) GiB" | tee -a "$O/times.txt"
  cp "$lab.fa" "$O/" 2>/dev/null
  check "$([ "$rc" = 0 ] && echo 1)" "$lab: muscle exit" "want 0" "$rc"
  fa2tsv "$lab.fa" > "$lab.tsv"
  check "$(same in.tsv "$lab.tsv")" "$lab: every row == its input, gaps removed" "names, residues" "$(awk 'END{print NR}' "$lab.tsv") rows, $(awk -F'\t' '{n+=length($2)} END{printf "%.0f", n}' "$lab.tsv") residues"
  check "$(same "$lab.tsv" <(fa2tsv "$MAFFT"))" "$lab: ungapped == mafft's, every record" "cross-tool content" "$(awk 'END{print NR}' "$lab.tsv") records"
  read -r mn mx <<<"$(lens "$lab.fa")"
  check "$([ "$mn" = "$mx" ] && [ "$mn" -gt 0 ] && echo 1)" "$lab: every row the same length" "min == max" "$mn..$mx columns"
  awk -f score.awk "$REF" "$lab.fa" > "$O/score-$lab.txt"
  check "$(awk '/^residues_unmapped/{print ($2==0)?1:0}' "$O/score-$lab.txt")" "$lab: every Pfam residue found" "0 unmapped" "$(awk '/^sp /{s=$2} /^tc /{t=$2} END{print "SP " s ", TC " t}' "$O/score-$lab.txt")"
  awk -f score.awk "$MAFFT" "$lab.fa" > "$O/vsmafft-$lab.txt"
  {
    echo "$lab mode $mode threads $T"
    echo "$lab columns $mx"
    echo "$lab wall_s $((t1-t0))"
    echo "$lab busy_cores $busy"
    echo "$lab memory_peak_cumulative_gib $(peak)"
    sed "s/^/$lab /" "$O/score-$lab.txt"
    awk -v l="$lab" '/^(sp|tc) /{print l " vs_mafft_" $1 " " $2}' "$O/vsmafft-$lab.txt"
    echo "$lab sha256 $(sha "$lab.fa")"
  } >> "$O/summary.txt"
  case $lab in
    *-rep)
      base=${lab%-rep}
      if [ "$(sha "$base.fa")" = "$(sha "$lab.fa")" ]; then s=1; else s=0; fi
      echo "$base two runs identical: $([ $s = 1 ] && echo yes || echo no)" | tee -a "$O/summary.txt"
      case " $ASSERT_SAME " in
        *" $base "*) check "$s" "$base: two runs byte-identical" "same sha256" "$(sha "$base.fa" | cut -c1-16) / $(sha "$lab.fa" | cut -c1-16)" ;;
      esac
      if [ $s = 0 ]; then
        awk -f score.awk "$base.fa" "$lab.fa" | awk -v l="$base" '/^(sp|tc) /{print l " rep_vs_first_" $1 " " $2}' | tee -a "$O/summary.txt"
      fi ;;
  esac
  PREV=$lab
done
echo "final cgroup memory.peak $(peak) GiB" | tee -a "$O/times.txt"
cat "$O/summary.txt"
if [ "$FAILS" = 0 ]; then echo "MUSCLE REAL OK" | tee -a "$O/checks.txt"; else echo "MUSCLE REAL FAILED: $FAILS" | tee -a "$O/checks.txt"; exit 1; fi
