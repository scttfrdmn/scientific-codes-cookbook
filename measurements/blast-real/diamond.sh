#!/usr/bin/env bash
# measurements/blast-real task 2 (DIAMOND image): the same queries against the same database, as
# exported by task 1 (both keyed by SwissProt OID), then compared with BLAST+'s self-hit table.
#   diamond.sh <threads>
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH HOME=/tmp TMPDIR=/tmp
O=/tmp/out; W=/var/tmp/dmnd
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  echo "diamond	$(diamond version 2>&1 | head -n1)"
  echo "diamond_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v diamond)" | tr -d ' ')"
} > $O/host.txt 2>&1
cat $O/host.txt

# Task 1's outputs, checked against the sums task 1 recorded for them.
QWANT=$(awk '$1=="queries_sha256"{print $2}' /tmp/blast-check.txt)
FWANT=$(awk '$1=="swissprot_fa_sha256"{print $2}' /tmp/blast-check.txt)
grep -q '^CHECKS PASSED' /tmp/blast-check.txt || fail "task 1 did not pass its checks"
[ "$(sha256sum /tmp/queries.fa | cut -d' ' -f1)" = "$QWANT" ] || fail "queries.fa is not task 1's"
gzip -dc /tmp/swissprot.fa.gz > $W/swissprot.fa
[ "$(sha256sum $W/swissprot.fa | cut -d' ' -f1)" = "$FWANT" ] || fail "swissprot.fa is not task 1's"
NQ=$(grep -c '^>' /tmp/queries.fa)

T0=$(date +%s)
diamond makedb --in $W/swissprot.fa -d $W/sp -p "$T" > $O/makedb.log 2>&1 || fail "makedb"
T1=$(date +%s)
# --very-sensitive is the mode comparable to blastp's default; same --max-target-seqs as task 1.
diamond blastp -q /tmp/queries.fa -d $W/sp --very-sensitive --max-target-seqs 500 -p "$T" \
  --outfmt 6 qseqid sseqid pident length qlen slen evalue bitscore -o $W/hits.tsv \
  > $O/diamond.log 2>&1 &
DP=$!
HWM=0
while kill -0 $DP 2>/dev/null; do
  h=$(awk '/^VmHWM/{print $2}' /proc/$DP/status 2>/dev/null); [ -n "$h" ] && HWM=$h
  sleep 2
done
wait $DP; RC=$?
T2=$(date +%s)
echo "makedb_s	$((T1-T0))	search_s	$((T2-T1))	rc	$RC	vmhwm_kb	$HWM" | tee $O/timing.txt
tail -n 20 $O/diamond.log
gzip -c $W/hits.tsv > $O/dmnd_hits.tsv.gz

# Self-row: subject OID == query OID. The cross-code claim is the SET of queries whose self-hit
# each tool recovers -- not top-hit or score equality, which measure tie-breaking and DIAMOND's
# heuristic scoring respectively.
awk -F'\t' '
  { q[$1]=1 }
  $1==$2 { sr[$1]=1; if ($3+0==100) s100[$1]=1; if ($3+0==100 && $4==$5) full[$1]=1 }
  END { for (k in q) nq++; for (k in sr) ns++; for (k in s100) n1++; for (k in full) nf++
        print "queries_with_hit\t" nq+0; print "self_rows\t" ns+0; print "self_100pct\t" n1+0
        print "full_length_self\t" nf+0
        for (k in sr) print k > "/var/tmp/dmnd/self.txt" }' $W/hits.tsv > $W/stats.txt
get() { awk -F'\t' -v k="$1" '$1==k{print $2}' $W/stats.txt; }
QH=$(get queries_with_hit); SR=$(get self_rows); S1=$(get self_100pct); FULL=$(get full_length_self)
BS=$(wc -l < /tmp/blast_self.tsv | tr -d ' ')
BOTH=$(awk -F'\t' 'FNR==NR{b[$1]=1;next} ($1 in b)' /tmp/blast_self.tsv $W/self.txt | wc -l | tr -d ' ')
DONLY=$((SR - BOTH)); BONLY=$((BS - BOTH))
ROWS=$(wc -l < $W/hits.tsv | tr -d ' ')
{
  echo "diamond_search_s    $((T2-T1))   ($T threads; makedb $((T1-T0)) s)"
  echo "queries             $NQ"
  echo "queries_with_hit    $QH"
  echo "self_rows           $SR   want == queries_with_hit (EXACT)"
  echo "self_100pct         $S1   want == self_rows (EXACT)"
  echo "full_length_self    $FULL   OBSERVATION"
  echo "blast_self_rows     $BS"
  echo "self_both           $BOTH"
  echo "self_blast_only     $BONLY   OBSERVATION"
  echo "self_diamond_only   $DONLY   OBSERVATION"
  echo "hit_rows            $ROWS"
  echo "hits_sha256         $(sha256sum $W/hits.tsv | cut -d' ' -f1)"
} > $O/check.txt
cat $O/check.txt
[ "$RC" -eq 0 ] || fail "diamond exited $RC"
[ "$QH" -gt 0 ] && [ "$SR" = "$QH" ] || fail "self_rows $SR != queries_with_hit $QH"
[ "$S1" = "$SR" ] || fail "self_100pct $S1 != self_rows $SR"
echo "CHECKS PASSED" | tee -a $O/check.txt
