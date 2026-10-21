set -eo pipefail
cd /tmp
echo "== verify staged inputs against their pinned sha256 =="
{ printf '%s  %s\n' 2d82087b6c5c60d762cc767f98e8260b273134c215ab7efccf7440614a4e5dab pfam38.2_A.hmm.gz
  printf '%s  %s\n' c753ba28b98b750659e27a7f9e0ceb224e26a5c767ce676a219c0a20adae5259 ensembl116_pep_one_per_gene.fa.gz
} > pins.sha256
sha256sum -c pins.sha256

# hmmsearch reads neither input gzipped. gzip is a base-image utility, not a second
# scientific tool: this is still one tool, one task.
gzip -dc pfam38.2_A.hmm.gz > Pfam-A.hmm
gzip -dc ensembl116_pep_one_per_gene.fa.gz > pep.fa
df -m /tmp | awk 'NR==2{print "tmpfs_total_mb "$2" used_mb "$3}'

echo "== hmmsearch: all 30,134 Pfam-A 38.2 families vs one protein per human gene, at Pfam's own GA thresholds =="
# --cut_ga uses each family's curated gathering threshold, which is how Pfam assignments
# are actually made; an E-value cut would be an arbitrary number of our own choosing.
# --noali drops the per-domain alignments, which at this scale are gigabytes of output
# nobody reads -- the trailer and the tblout survive, and those are what is checked.
T0=$(date +%s)
hmmsearch --cpu 8 --cut_ga --noali --tblout hits.tbl -o hmmer.out Pfam-A.hmm pep.fa
T1=$(date +%s); WALL=$(( T1 - T0 ))

MODELS=$(grep -c '^Query:' hmmer.out || true)
TARGETS=$(awk '/Target sequences:/ {print $3; exit}' hmmer.out)
HITS=$(grep -vc '^#' hits.tbl || true)
FAMS=$(grep -v '^#' hits.tbl | awk '{print $3}' | sort -u | wc -l | tr -d ' ')
PROTS=$(grep -v '^#' hits.tbl | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
# hmmsearch writes a literal "[ok]" as its last line only on clean completion. This is the
# check that catches a search killed part-way, whose partial tblout would pass any count band.
TRAILER=$(tail -n 1 hmmer.out)
PCT=$(awk -v p="$PROTS" -v t="$TARGETS" 'BEGIN{printf "%.2f", 100*p/t}')

{
  echo "models_searched     $MODELS   want exactly 30134  (every family in Pfam-A 38.2)"
  echo "target_sequences    $TARGETS   want exactly 23879  (one protein per gene)"
  echo "hmmsearch_trailer   $TRAILER   want exactly [ok]   [completion sentinel]"
  echo "tblout_hits         $HITS   want exactly 65607"
  echo "families_with_hit   $FAMS   want exactly 9473"
  echo "proteins_with_hit   $PROTS   want exactly 22740  ($PCT% of genes carry >=1 Pfam domain)"
  echo "wall_s              $WALL"
} > smoke-check.txt
cat smoke-check.txt
test "$MODELS"  -eq 30134
test "$TARGETS" -eq 23879
test "$TRAILER"  = "[ok]"
test "$HITS"  -eq 65607
test "$FAMS"  -eq 9473
test "$PROTS" -eq 22740
test -s hits.tbl
gzip -n -6 hits.tbl
echo "SMOKE CHECK PASSED"
