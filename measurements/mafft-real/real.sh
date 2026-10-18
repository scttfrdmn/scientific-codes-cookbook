#!/usr/bin/env bash
# mafft-real (MAFFT image): align the whole PF01255.25 seed, unaligned, three ways --
#   linsi     mafft --localpair --maxiterate 1000 --threadit 0   L-INS-i, the workload; refinement
#             single-threaded, which is what makes it reproducible. Run twice: must be identical.
#   linsi-mt  the same without --threadit 0: refinement threaded too. Run twice: reported.
#   auto      mafft --auto: what mafft picks for itself (FFT-NS-2 here), seconds
# -- then check each alignment against its own input and against Pfam's curated seed alignment.
#
#   bash real.sh <unaligned.fa> <ref.fa> <threads>
#
# Writes everything to /tmp/out/. Runs on the aarch.bio image and on bioconda's x86 one (BusyBox:
# no python, no cmp guaranteed, no date +%N), so the glue is POSIX awk.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
IN=$1 REF=$2 T=$3
O=/tmp/out
mkdir -p "$O" /tmp/mafft-tmp
export TMPDIR=/tmp/mafft-tmp
cd /tmp
FAILS=0
row() { printf '%-44s %-26s %s\n' "$1" "$2" "$3" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "ok   ($4)"; else row "$2" "$3" "FAIL ($4)"; FAILS=$((FAILS+1)); fi; }

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  b=$(find /opt /usr -name dvtditr -path '*mafft*' 2>/dev/null | head -n 1)
  echo "dvtditr $b links: $( (ldd "$b" 2>&1 || echo none) | awk '{printf "%s ", $1}')"
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "mafft $(command -v mafft) $(mafft --version 2>&1 | head -n 1)"
} > "$O/host.txt"
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"

# footprint every 5 s: anonymous memory (RSS, not page cache), cumulative cgroup CPU, and /tmp
# (tmpfs, half the RAM) used -- mafft keeps its pairwise work files in TMPDIR. Echoed each minute
# so a killed run still leaves its high-water marks in command.log.
( i=0; while : ; do
    s="$(date +%s) $(awk '/^anon /{print $2; exit}' /sys/fs/cgroup/memory.stat 2>/dev/null) $(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null) $(df -k /tmp | awk 'NR==2{print $3}')"
    echo "$s" >> "$O/samples.txt"
    [ $((i % 12)) = 0 ] && echo "sample $s"
    i=$((i+1)); sleep 5
  done ) &
SPID=$!

# "name<TAB>UPPERCASE residues" per record, in file order: what an alignment must preserve
fa2tsv() { awk '/^>/{if(n!="")print n"\t"s; n=substr($1,2); s=""; next} {g=$0; gsub(/[-.]/,"",g); s=s toupper(g)} END{if(n!="")print n"\t"s}' "$1"; }
same() { awk 'NR==FNR{a[FNR]=$0; n=FNR; next} {if(a[FNR]!=$0)bad++; m=FNR} END{print (n>0 && bad==0 && n==m)?1:0}' "$1" "$2"; }
lens() { awk '/^>/{if(h)print l; h=1; l=0; next} {l+=length($0)} END{if(h)print l}' "$1" | awk 'NR==1{mn=$1} {if($1<mn)mn=$1; if($1>mx)mx=$1} END{print mn+0, mx+0}'; }

# Score an alignment against Pfam's: every pair of residues Pfam puts in one column is a reference
# pair; SP is the fraction the test alignment also puts in one column, TC the fraction of Pfam's
# columns (>= 2 residues) it reproduces whole. Integer counts, so exact. (Uppercase = scored: the
# Pfam seed is all uppercase, so every column scores.)
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
awk -F'\t' '{print $1}' in.tsv | sort | uniq -d > dup.txt
check "$([ ! -s dup.txt ] && echo 1)" "record names unique" "0 duplicates" "$(awk 'END{print NR}' dup.txt) duplicates"
awk -f score.awk "$REF" "$REF" > "$O/score-ref-vs-ref.txt"
check "$(awk '/^sp /{a=$2} /^tc /{b=$2} END{print (a=="1.000000" && b=="1.000000")?1:0}' "$O/score-ref-vs-ref.txt")" "scorer control: Pfam vs itself" "SP = TC = 1 exactly" "$(tr '\n' ' ' < "$O/score-ref-vs-ref.txt" | cut -c1-80)"

align() {   # <label> <mafft args...>
  lab=$1; shift
  u0=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
  t0=$(date +%s)
  mafft "$@" --thread "$T" "$IN" > "$lab.fa" 2> "$O/$lab.log"
  rc=$?
  t1=$(date +%s)
  u1=$(awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
  busy=$(awk -v a="$u0" -v b="$u1" -v s=$((t1-t0)) 'BEGIN{printf "%.2f", (s>0 && b!="")?(b-a)/1e6/s:0}')
  echo "$lab: mafft $* --thread $T exit $rc in $((t1-t0)) s, $busy cores busy" | tee -a "$O/times.txt"
  cp "$lab.fa" "$O/"
  check "$([ "$rc" = 0 ] && echo 1)" "$lab: mafft exit" "want 0" "$rc"
  fa2tsv "$lab.fa" > "$lab.tsv"
  check "$(same in.tsv "$lab.tsv")" "$lab: every row == its input, gaps removed" "names, order, residues" "$(awk 'END{print NR}' "$lab.tsv") rows, $(awk -F'\t' '{n+=length($2)} END{printf "%.0f", n}' "$lab.tsv") residues"
  read -r mn mx <<<"$(lens "$lab.fa")"
  check "$([ "$mn" = "$mx" ] && [ "$mn" -gt 0 ] && echo 1)" "$lab: every row the same length" "min == max" "$mn..$mx columns"
  awk -f score.awk "$REF" "$lab.fa" > "$O/score-$lab.txt"
  check "$(awk '/^residues_unmapped/{print ($2==0)?1:0}' "$O/score-$lab.txt")" "$lab: every Pfam residue found" "0 unmapped" "$(awk '/^sp /{s=$2} /^tc /{t=$2} END{print "SP " s ", TC " t}' "$O/score-$lab.txt")"
  {
    echo "$lab columns $mx"
    echo "$lab strategy $(awk '/^Strategy:/{getline; print $1; exit}' "$O/$lab.log")"
    echo "$lab wall_s $((t1-t0))"
    echo "$lab busy_cores $busy"
    echo "$lab refinement_threads $(tr '\r' '\n' < "$O/$lab.log" | awk '/^dvtditr/{f=1} f && /thread\(s\)/{print $1; exit}')"
    sed "s/^/$lab /" "$O/score-$lab.txt"
    echo "$lab sha256 $(sha256sum "$lab.fa" | cut -d' ' -f1)"
  } >> "$O/summary.txt"
}

sha() { sha256sum "$1" | cut -d' ' -f1; }
echo "== L-INS-i, refinement single-threaded (--threadit 0), twice =="
align linsi --localpair --maxiterate 1000 --threadit 0
align linsi-rep --localpair --maxiterate 1000 --threadit 0
check "$([ "$(sha linsi.fa)" = "$(sha linsi-rep.fa)" ] && echo 1)" "linsi: two runs byte-identical" "same sha256" "$(sha linsi.fa | cut -c1-16) / $(sha linsi-rep.fa | cut -c1-16)"
echo "== L-INS-i, refinement threaded too, twice (reported, not asserted) =="
align linsi-mt --localpair --maxiterate 1000
align linsi-mt-rep --localpair --maxiterate 1000
echo "linsi-mt two runs identical: $([ "$(sha linsi-mt.fa)" = "$(sha linsi-mt-rep.fa)" ] && echo yes || echo no)" | tee -a "$O/summary.txt"
echo "== --auto =="
align auto --auto
kill $SPID 2>/dev/null
awk 'NR==1{t0=$1; u0=$3} $2>m{m=$2} $4>tm{tm=$4} {t=$1; u=$3} END{printf "peak_anon_gib %.2f\npeak_tmp_gib %.2f\navg_busy_cores %.2f\n", m/1073741824, tm/1048576, (t>t0)?(u-u0)/1e6/(t-t0):0}' "$O/samples.txt" | tee -a "$O/times.txt"
cat "$O/summary.txt"
if [ "$FAILS" = 0 ]; then echo "MAFFT REAL OK" | tee -a "$O/checks.txt"; else echo "MAFFT REAL FAILED: $FAILS" | tee -a "$O/checks.txt"; exit 1; fi
