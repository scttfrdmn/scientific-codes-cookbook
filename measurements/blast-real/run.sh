#!/usr/bin/env bash
# measurements/blast-real task 1 (BLAST+ image): the whole reviewed human proteome -- every
# SwissProt entry carrying taxid 9606 -- searched with blastp against all of NCBI's preformatted
# SwissProt, the database the queries were drawn from. Writes the hit table, the self-hit table
# and the checks to /tmp/out, and both FASTAs DIAMOND needs (task 2) to /tmp/out as well.
#   run.sh <threads>
# Glue is BusyBox-safe: bioconda's x86 image has no lscpu, perl, join or sha256sum --quiet.
set +e
set -uo pipefail
T=${1:?threads}
export PATH=/usr/local/bin:/opt/conda/bin:$PATH BLASTDB=/tmp/db HOME=/tmp TMPDIR=/tmp
O=/tmp/out; W=/var/tmp/blast        # big intermediates on the root disk, not the half-RAM tmpfs
mkdir -p $O $W
fail() { echo "FAIL: $*" | tee -a $O/check.txt; exit 1; }

{
  echo "uname_m	$(uname -m)"
  echo "nproc	$(nproc)"
  echo "threads_per_core	$(awk -F'[,-]' '{print NF}' /sys/devices/system/cpu/cpu0/topology/thread_siblings_list 2>/dev/null)"
  grep -m1 -E '^model name' /proc/cpuinfo | sed 's/^model name[^:]*: */cpu_model	/'
  grep -m1 -E '^CPU part' /proc/cpuinfo | sed 's/^CPU part[^:]*: */cpu_part	/'
  echo "mem_total_kb	$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  echo "blastp	$(blastp -version 2>&1 | head -n1)"
  echo "blastp_elf_machine	$(od -An -tu2 -j18 -N2 "$(command -v blastp)" | tr -d ' ')"
  echo "openblas	not linked (BLAST+ is integer/heuristic search; no BLAS)"
} > $O/host.txt 2>&1
cat $O/host.txt

echo "== staged database must match its pins =="
( cd /tmp/db && sha256sum -c /tmp/inputs.sha256 ) > $O/pins-check.txt 2>&1 \
  || { cat $O/pins-check.txt; fail "a staged database file does not match its pin"; }
cat $O/pins-check.txt

blastdbcmd -db swissprot -info > $O/dbinfo.txt 2>&1 || fail "blastdbcmd -info"
cat $O/dbinfo.txt
DBSEQ=$(awk '/sequences;/ {gsub(/,/,"",$1); print $1; exit}' $O/dbinfo.txt)
DBLET=$(awk '/total residues/ {gsub(/,/,"",$3); print $3; exit}' $O/dbinfo.txt)

echo "== queries: every SwissProt entry with taxid 9606, keyed by OID =="
# -no_taxid_expansion: 9606 itself. Expanded, -taxids 9606 also returns OIDs whose only deflines
# carry a descendant taxid, which are not the human proteome (expansion-only.tsv lists them).
blastdbcmd -db swissprot -taxids 9606 -no_taxid_expansion -outfmt '%o	%a	%T	%s' > $W/human.tsv \
  2> $O/blastdbcmd.log || { cat $O/blastdbcmd.log; fail "blastdbcmd -taxids 9606"; }
NEXP=$(blastdbcmd -db swissprot -taxids 9606 -outfmt '%o' 2>> $O/blastdbcmd.log | sort -u | wc -l | tr -d ' ')
blastdbcmd -db swissprot -taxids 9606 -outfmt '%o	%T	%a' 2>> $O/blastdbcmd.log \
  | awk -F'\t' 'FNR==NR{h[$1]=1;next} !($1 in h)' $W/human.tsv - > $O/expansion-only.tsv
NREC=$(wc -l < $W/human.tsv | tr -d ' ')
NOID=$(cut -f1 $W/human.tsv | sort -u | wc -l | tr -d ' ')
# blastdbcmd prints one row per DEFLINE of each matching OID. The database is non-redundant --
# identical sequences share one OID -- so a human protein identical to another species' is one OID
# whose other deflines carry other taxids. Every OID must carry at least one 9606 defline.
NNOT=$(awk -F'\t' '$3!=9606' $W/human.tsv | wc -l | tr -d ' ')
NHUM=$(awk -F'\t' '$3==9606{print $1}' $W/human.tsv | sort -u | wc -l | tr -d ' ')
# one query per OID
awk -F'\t' '$3==9606 && !s[$1]++ { print "o"$1"\t"$2"\t"length($4) }' $W/human.tsv > $O/queries.tsv
awk -F'\t' '$3==9606 && !s[$1]++ { print ">o"$1; print $4 }' $W/human.tsv > $O/queries.fa
NQ=$(wc -l < $O/queries.tsv | tr -d ' ')
QLET=$(awk -F'\t' '{s+=$3} END{print s}' $O/queries.tsv)
echo "records=$NREC oids=$NOID queries=$NQ letters=$QLET other_species_deflines=$NNOT human_oids=$NHUM with_expansion=$NEXP"

echo "== the whole database as FASTA, keyed by OID, for DIAMOND (task 2) =="
blastdbcmd -db swissprot -entry all -outfmt '%o	%s' 2>> $O/blastdbcmd.log \
  | awk -F'\t' '{ print ">o"$1; print $2 }' > $W/swissprot.fa
NFA=$(grep -c '^>' $W/swissprot.fa)
gzip -c $W/swissprot.fa > $O/swissprot.fa.gz

echo "== blastp: $NQ queries vs all of SwissProt, $T threads =="
T0=$(date +%s)
blastp -query $O/queries.fa -db swissprot -num_threads "$T" -max_target_seqs 500 \
  -outfmt '6 qseqid sallacc pident length qlen slen evalue bitscore' -out $W/hits.tsv \
  > $O/blastp.log 2>&1 &
BP=$!
# VmHWM is the kernel's own high-water mark for the process, so reading it late misses nothing but
# the last interval's growth -- unlike sampling VmRSS, which misses a brief peak entirely.
HWM=0
while kill -0 $BP 2>/dev/null; do
  h=$(awk '/^VmHWM/{print $2}' /proc/$BP/status 2>/dev/null); [ -n "$h" ] && HWM=$h
  sleep 2
done
wait $BP; RC=$?
echo "blastp_vmhwm_kb	$HWM" > $O/memory-blastp.txt
T1=$(date +%s); WALL=$(( T1 - T0 ))
echo "blastp_rc	$RC	wall_s	$WALL" | tee $O/timing.txt
cat /sys/fs/cgroup/memory.peak > $O/memory-peak.txt 2>/dev/null || echo unreadable > $O/memory-peak.txt
echo "cgroup_memory_peak_bytes	$(cat $O/memory-peak.txt)" | tee -a $O/timing.txt
tail -n 20 $O/blastp.log
HSHA=$(sha256sum $W/hits.tsv | cut -d' ' -f1)
QSHA=$(sha256sum $O/queries.fa | cut -d' ' -f1)
FSHA=$(sha256sum $W/swissprot.fa | cut -d' ' -f1)
gzip -c $W/hits.tsv > $O/hits.tsv.gz

# ---- checks ----
# A self-row: a row whose subject's accession list contains the query's own accession (version
# stripped -- sallacc prints none). The queries ARE database records, so each one that hits anything
# must hit itself, and a sequence is 100.000% identical to itself: both exact. Whether the self
# alignment spans the whole query, and whether the self-hit is the top score, are observations
# (the recipe's own 1000-query run found one-residue-short self-alignments).
awk -F'\t' '
  FNR==NR { a=$2; sub(/\.[0-9]+$/,"",a); acc[$1]=a; ql[$1]=$3; next }
  { q[$1]=1; if (!($1 in best) || $8+0 > best[$1]) best[$1]=$8+0
    n=split($2, s, ";"); self=0
    for (i=1;i<=n;i++) if (s[i]==acc[$1]) self=1
    if (self) { sr[$1]=1; if ($3+0==100) s100[$1]=1; if ($3+0==100 && $4==$5) full[$1]=1
                if (!($1 in sbest) || $8+0 > sbest[$1]) sbest[$1]=$8+0 } }
  END {
    for (k in q) nq++; for (k in sr) ns++; for (k in s100) n1++; for (k in full) nf++
    for (k in sr) if (sbest[k] >= best[k]) top++
    print "queries_with_hit\t" nq+0; print "self_rows\t" ns+0; print "self_100pct\t" n1+0
    print "full_length_self\t" nf+0; print "self_is_top_score\t" top+0
    for (k in sr) print k "\t" acc[k] "\t" ((k in s100)?1:0) "\t" ((k in full)?1:0) > "/tmp/out/blast_self.tsv"
  }' $O/queries.tsv $W/hits.tsv > $W/stats.txt
cat $W/stats.txt
get() { awk -F'\t' -v k="$1" '$1==k{print $2}' $W/stats.txt; }
QH=$(get queries_with_hit); SR=$(get self_rows); S1=$(get self_100pct)
FULL=$(get full_length_self); TOP=$(get self_is_top_score)
ROWS=$(wc -l < $W/hits.tsv | tr -d ' ')
MISS=$(awk -F'\t' 'FNR==NR{h[$1]=1;next} !($1 in h){print $3}' $W/hits.tsv $O/queries.tsv | sort -n | tr '\n' ' ')
{
  echo "blastp_wall_s       $WALL   ($T threads)"
  echo "db_sequences        $DBSEQ   want == 487728 (NCBI's published count for this snapshot)"
  echo "db_letters          $DBLET   want == 186063409 (NCBI's published count)"
  echo "fasta_records       $NFA   want == db_sequences (every OID exported)"
  echo "query_oids          $NQ   want == distinct OIDs ($NOID) == OIDs with a 9606 defline ($NHUM); $NREC deflines, $NNOT of other species; $NEXP OIDs with taxid expansion"
  echo "query_letters       $QLET"
  echo "queries_with_hit    $QH"
  echo "self_rows           $SR   want == queries_with_hit (EXACT)"
  echo "self_100pct         $S1   want == self_rows (EXACT)"
  echo "full_length_self    $FULL   OBSERVATION"
  echo "self_is_top_score   $TOP   OBSERVATION (of self_rows)"
  echo "queries_no_hit_lens $MISS"
  echo "hit_rows            $ROWS"
  echo "hits_sha256         $HSHA"
  echo "queries_sha256      $QSHA"
  echo "swissprot_fa_sha256 $FSHA"
} > $O/check.txt
cat $O/check.txt
[ "$RC" -eq 0 ] || fail "blastp exited $RC"
[ "$DBSEQ" = 487728 ] || fail "db_sequences $DBSEQ"
[ "$DBLET" = 186063409 ] || fail "db_letters $DBLET"
[ "$NFA" = "$DBSEQ" ] || fail "fasta_records $NFA"
[ "$NQ" = "$NOID" ] && [ "$NHUM" = "$NOID" ] && [ "$NQ" -gt 15000 ] || fail "query extraction"
[ "$QH" -gt 0 ] && [ "$SR" = "$QH" ] || fail "self_rows $SR != queries_with_hit $QH"
[ "$S1" = "$SR" ] || fail "self_100pct $S1 != self_rows $SR"
echo "CHECKS PASSED" | tee -a $O/check.txt
