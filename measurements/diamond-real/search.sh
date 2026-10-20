#!/usr/bin/env bash
# measurements/diamond-real: every reviewed human protein (UniProt 2026_03 Swiss-Prot, OX=9606)
# searched with DIAMOND --very-sensitive against the whole of UniRef50 2026_03 (38.8M clusters),
# the recipe's invocation at a database BLAST+ is not run against. Runs in the recipe's DIAMOND
# image on every chip (aarch.bio arm64, bioconda x86 -- the x86 one has a BusyBox userland, so
# no join/numfmt/perl/`sha256sum --quiet` here).
#   search.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/var/tmp
O=/tmp/out; W=/var/tmp/dr
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }
peak() {  # peak() <pid>: the process's VmHWM (kB), read until it exits
  local h=0 x
  while kill -0 "$1" 2>/dev/null; do
    x=$(awk '/^VmHWM/{print $2}' /proc/$1/status 2>/dev/null); [ -n "$x" ] && h=$x
    sleep 1
  done
  echo $h
}

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "diamond	$(diamond version 2>&1 | head -n1)"
  echo "diamond_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v diamond)" | tr -d ' ')"
} > $O/host.txt 2>&1
cat $O/host.txt
M=$(awk -F'\t' '$1=="diamond_elf_machine"{print $2}' $O/host.txt)
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=? ;; esac
[ "$M" = "$WANT" ] || fail "diamond is ELF machine $M on $(uname -m): emulated or wrong image"

echo "== staged inputs against pins.sha256 =="
cd /tmp
grep -E ' (uniref50\.fasta\.gz|uniprot_sprot\.fasta\.gz|HUMAN_9606_idmapping_selected\.tab\.gz)$' pins.sha256 \
  > in.sha256
[ "$(wc -l < in.sha256 | tr -d ' ')" = 3 ] || fail "pins.sha256 lacks an input"
sha256sum -c in.sha256 || fail "a staged input does not match its pin"
NOTE=38840027   # uniref50.release_note, 2026_03: "Number of clusters: 38,840,027"
NREV=20431      # UniProt 2026_03: reviewed entries with organism_id 9606

echo "== queries: Swiss-Prot entries with OX=9606, renamed to their accession =="
gzip -dc uniprot_sprot.fasta.gz | awk '
  /^>/ { keep = ($0 ~ / OX=9606( |$)/); if (keep) { split($1, a, "|"); print ">" a[2] }; next }
  keep' > $W/queries.fa
NQ=$(grep -c '^>' $W/queries.fa)
awk '/^>/{if (id) print id "\t" n; id=substr($1,2); n=0; next} {n+=length($0)} END{if (id) print id "\t" n}' \
  $W/queries.fa > $W/qlen.tsv
QD=$(cut -f1 $W/qlen.tsv | sort | uniq -d | wc -l | tr -d ' ')
QL=$(awk -F'\t' '{s+=$2} END{print s}' $W/qlen.tsv)

echo "== UniProt's own clustering: each query's UniRef50 cluster (idmapping column 10) =="
gzip -dc HUMAN_9606_idmapping_selected.tab.gz | awk -F'\t' 'FNR==NR{q[$1]=1; next} ($1 in q){print $1 "\t" $10}' \
  $W/qlen.tsv - > $W/cluster.tsv
NMAP=$(awk -F'\t' '$2 ~ /^UniRef50_/' $W/cluster.tsv | wc -l | tr -d ' ')
# UniRef leaves out sequences shorter than 11 residues, so exactly those queries have no cluster.
SHORT=$(awk -F'\t' '$2 < 11' $W/qlen.tsv | wc -l | tr -d ' ')
UNMAP_LONG=$(awk -F'\t' 'FNR==NR{len[$1]=$2; next} $2 !~ /^UniRef50_/ && len[$1] >= 11' $W/qlen.tsv $W/cluster.tsv | wc -l | tr -d ' ')

echo "== independent count of the database (before makedb, so its timing is clean) =="
TC0=$(date +%s)
gzip -dc uniref50.fasta.gz | grep '^>' | cut -d' ' -f1 | cut -c2- > $W/ids.txt
gzip -dc uniref50.fasta.gz | grep -v '^>' | tr -d '\n' | wc -c | tr -d ' ' > $W/letters.txt
echo "independent_count_s	$(( $(date +%s) - TC0 ))"

echo "== diamond makedb: UniRef50 2026_03 =="
T0=$(date +%s)
diamond makedb --in uniref50.fasta.gz -d $W/uniref50 -p "$T" > $O/makedb.log 2>&1 &
DP=$!
MKHWM=$(peak $DP); wait $DP; MKRC=$?
T1=$(date +%s)
tail -n 8 $O/makedb.log
[ "$MKRC" -eq 0 ] || fail "makedb exited $MKRC"
diamond dbinfo -d $W/uniref50.dmnd > $O/dbinfo.txt 2>&1
cat $O/dbinfo.txt
DBSEQ=$(awk '$1=="Sequences"{print $NF}' $O/dbinfo.txt | tr -cd '0-9')
DBLET=$(awk '$1=="Letters"{print $NF}' $O/dbinfo.txt | tr -cd '0-9')
NH=$(wc -l < $W/ids.txt | tr -d ' ')
IDDUP=$(sort $W/ids.txt | uniq -d | wc -l | tr -d ' ')
LET=$(cat $W/letters.txt)
# A query is in the database verbatim-by-name when it is its cluster's representative.
awk -F'\t' 'FNR==NR{r["UniRef50_" $1]=$1; next} ($1 in r){print r[$1]}' $W/qlen.tsv $W/ids.txt | sort > $W/reps.txt
NREP=$(wc -l < $W/reps.txt | tr -d ' ')
# Every cluster UniProt maps a query to must exist in the database.
# (The ~20k mapped clusters go in the hash, not the 38.8M ids: BusyBox awk's hash table stops
# growing at 65,521 buckets, so hashing the ids takes hours on the x86 image.)
MISS=$(awk -F'\t' 'FNR==NR{ if ($2 != "") need[$2]=1; next } ($1 in need){ got[$1]=1 }
  END{ for (k in need) if (!(k in got)) n++; print n+0 }' $W/cluster.tsv $W/ids.txt)
# A representative query's own cluster is the one named after it.
REPMIS=$(awk -F'\t' 'FNR==NR{r[$1]=1; next} ($1 in r) && $2!="UniRef50_" $1' $W/reps.txt $W/cluster.tsv | wc -l | tr -d ' ')
DMND_SHA=$(sha256sum $W/uniref50.dmnd | cut -d' ' -f1)

echo "== diamond blastp --very-sensitive: every human Swiss-Prot protein vs UniRef50 =="
T2=$(date +%s)
diamond blastp -q $W/queries.fa -d $W/uniref50 --very-sensitive --max-target-seqs 500 -p "$T" \
  --tmpdir $W --outfmt 6 qseqid sseqid pident length qlen slen evalue bitscore -o $W/hits.tsv \
  > $O/diamond.log 2>&1 &
DP=$!
HWM=$(peak $DP); wait $DP; RC=$?
T3=$(date +%s)
CGPEAK=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
echo "makedb_s	$((T1-T0))	makedb_vmhwm_kb	$MKHWM	search_s	$((T3-T2))	rc	$RC	vmhwm_kb	$HWM	cgroup_memory_peak	$CGPEAK" \
  | tee $O/timing.txt
tail -n 6 $O/diamond.log
[ "$RC" -eq 0 ] || fail "diamond blastp exited $RC"
ALIGNED=$(awk '/queries aligned/{gsub("'"'"'","",$1); print $1}' $O/diamond.log)

echo "== checks =="
# qlen conservation, self rows (subject is UniRef50_<query>), and recovery of UniProt's own cluster.
awk -F'\t' -v W=$W '
  FILENAME==W"/qlen.tsv" { len[$1]=$2; next }
  FILENAME==W"/cluster.tsv" { cl[$1]=$2; next }
  { rows++; q[$1]=1
    if (!($1 in len) || $5 != len[$1]) badlen++
    if ($2 == "UniRef50_" $1) { sr[$1]=1; if ($3+0 == 100) s100[$1]=1
                                if ($3+0 == 100 && $4 == $5 && $5 == $6) full[$1]=1 }
    if (($1 in cl) && $2 == cl[$1]) own[$1]=1 }
  END { for (k in q) nq++; for (k in sr) ns++; for (k in s100) n1++; for (k in full) nf++; for (k in own) no++
        print "hit_rows\t" rows+0; print "queries_with_hit\t" nq+0; print "qlen_mismatch_rows\t" badlen+0
        print "self_rows\t" ns+0; print "self_100pct\t" n1+0; print "full_length_self\t" nf+0
        print "own_cluster_hit\t" no+0
        for (k in q) print k > W"/hit_queries.txt"; for (k in sr) print k > W"/self.txt" }' \
  $W/qlen.tsv $W/cluster.tsv $W/hits.tsv > $W/stats.txt
get() { awk -F'\t' -v k="$1" '$1==k{print $2}' $W/stats.txt; }
ROWS=$(get hit_rows); QH=$(get queries_with_hit); BADLEN=$(get qlen_mismatch_rows)
SR=$(get self_rows); S1=$(get self_100pct); FULL=$(get full_length_self); OWN=$(get own_cluster_hit)
# representatives that hit anything, and how many of those found themselves
REPH=$(awk 'FNR==NR{h[$1]=1; next} ($1 in h)' $W/hit_queries.txt $W/reps.txt | wc -l | tr -d ' ')
SELFREP=$(awk 'FNR==NR{s[$1]=1; next} ($1 in s)' $W/self.txt $W/reps.txt | wc -l | tr -d ' ')
# Cross-chip fingerprints: the table as written, and the order-free set of (query, subject) pairs.
HITS_SHA=$(sha256sum $W/hits.tsv | cut -d' ' -f1)
cut -f1,2 $W/hits.tsv | sort -u > $W/pairs.tsv
PAIRS=$(wc -l < $W/pairs.tsv | tr -d ' ')
PAIRS_SHA=$(sha256sum $W/pairs.tsv | cut -d' ' -f1)
SELF_SHA=$(sort $W/self.txt | sha256sum | cut -d' ' -f1)
gzip -c $W/hits.tsv > $O/hits.tsv.gz
gzip -c $W/pairs.tsv > $O/pairs.tsv.gz
sort $W/self.txt > $O/self.txt
cp $W/reps.txt $W/cluster.tsv $W/qlen.tsv $O/

{
  echo "makedb_s            $((T1-T0))"
  echo "search_s            $((T3-T2))   ($T threads)"
  echo "db_sequences        $DBSEQ   want == release note $NOTE == FASTA headers (EXACT)"
  echo "fasta_headers       $NH"
  echo "duplicate_db_ids    $IDDUP   want 0"
  echo "db_letters          $DBLET   want == residues counted from the FASTA (EXACT)"
  echo "fasta_residues      $LET"
  echo "dmnd_sha256         $DMND_SHA"
  echo "queries             $NQ   want == UniProt's $NREV reviewed human entries (EXACT)"
  echo "duplicate_queries   $QD   want 0"
  echo "query_letters       $QL"
  echo "queries_mapped      $NMAP   want == queries - short_queries"
  echo "short_queries       $SHORT   (< 11 residues: UniRef leaves these out)"
  echo "unmapped_long       $UNMAP_LONG   want 0 (every query of >= 11 residues has a UniRef50 cluster)"
  echo "mapped_cluster_absent $MISS   want 0 (every cluster named by idmapping is in the FASTA)"
  echo "representative_queries $NREP"
  echo "rep_cluster_mismatch $REPMIS   want 0 (a representative's cluster is named after it)"
  echo "queries_aligned_log $ALIGNED   want == queries_with_hit (EXACT)"
  echo "queries_with_hit    $QH"
  echo "hit_rows            $ROWS"
  echo "qlen_mismatch_rows  $BADLEN   want 0 (every row's qlen == its query's length)"
  echo "reps_with_hit       $REPH"
  echo "self_rows           $SR"
  echo "self_reps           $SELFREP   want == reps_with_hit (EXACT: a representative that hits anything hits itself)"
  echo "self_100pct         $S1   want == self_rows (EXACT)"
  echo "full_length_self    $FULL   OBSERVATION"
  echo "own_cluster_hit     $OWN   OBSERVATION (queries whose hits include UniProt's own cluster for them)"
  echo "hits_sha256         $HITS_SHA"
  echo "pairs               $PAIRS"
  echo "pairs_sha256        $PAIRS_SHA"
  echo "self_set_sha256     $SELF_SHA"
} > $O/check.txt
cat $O/check.txt
[ "$DBSEQ" = "$NOTE" ] && [ "$NH" = "$NOTE" ] || fail "database sequences $DBSEQ / headers $NH != $NOTE"
[ "$IDDUP" = 0 ] || fail "$IDDUP duplicate database ids"
[ -n "$DBLET" ] && [ "$DBLET" = "$LET" ] || fail "database letters $DBLET != FASTA residues $LET"
[ "$NQ" = "$NREV" ] || fail "queries $NQ != $NREV"
[ "$QD" = 0 ] || fail "$QD duplicate query accessions"
[ "$UNMAP_LONG" = 0 ] && [ "$NMAP" = "$((NQ - SHORT))" ] || fail "$NMAP of $NQ queries map to a cluster ($SHORT short, $UNMAP_LONG long unmapped)"
[ "$MISS" = 0 ] || fail "$MISS mapped clusters absent from the database"
[ "$REPMIS" = 0 ] || fail "$REPMIS representatives mapped to another cluster"
[ "$QH" -gt 0 ] && [ "$ALIGNED" = "$QH" ] || fail "log says $ALIGNED queries aligned, table has $QH"
[ "$BADLEN" = 0 ] || fail "$BADLEN rows with a qlen that is not the query's length"
[ "$SELFREP" = "$REPH" ] && [ "$SR" = "$SELFREP" ] || fail "self rows $SR / reps finding themselves $SELFREP != reps with a hit $REPH"
[ "$S1" = "$SR" ] || fail "self_100pct $S1 != self_rows $SR"
echo "CHECKS PASSED" | tee -a $O/check.txt
