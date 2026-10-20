#!/usr/bin/env bash
# foldseek-real diagnostic: are the hits one chip's build finds and the other's misses lost in the
# PREFILTER (candidate selection) or in the ALIGNMENT (scoring + the -e 10 cutoff)?
#
#   bash diag.sh <threads>        # reads /tmp/pairs.tsv: side, query, target (from compare.sh)
#
# Re-searches every query involved with --exhaustive-search 1, which skips the prefilter and aligns
# each query against all 23,586 entries, at the run's other defaults (-e 10). If both builds then
# find a pair, the prefilter dropped it; if one still does not, the alignment score decided it.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
T=$1; O=/tmp/out; W=/var/tmp/diag
mkdir -p "$O" "$W"; cd "$W"
FMT="query,target,fident,alnlen,mismatch,gapopen,qstart,qend,tstart,tend,qlen,tlen,evalue,bits,alntmscore,qtmscore,ttmscore,lddt,prob"
uname -m > "$O/arch.txt"
foldseek createdb /tmp/UP000005640_9606_HUMAN_v6.tar db --file-include '.*\.cif\.gz$' --threads "$T" > "$O/diag.log" 2>&1
cut -f2 /tmp/pairs.tsv | sort -u > q.names
awk 'NR==FNR{k[$1]; next} ($2 in k){print $1}' q.names db.lookup > q.keys
for s in "" _ss _ca _h; do foldseek createsubdb q.keys "db$s" "q$s" >> "$O/diag.log" 2>&1; done
foldseek search q db aln tmp -a --exhaustive-search 1 --threads "$T" >> "$O/diag.log" 2>&1
foldseek convertalis q db aln exh.tsv --format-output "$FMT" --threads "$T" >> "$O/diag.log" 2>&1
sort exh.tsv > "$O/exhaustive.tsv"
printf 'queries\t%s\nexhaustive_rows\t%s\n' "$(wc -l < q.keys | tr -d ' ')" "$(wc -l < exh.tsv | tr -d ' ')" | tee "$O/diag.tsv"
# for each disagreeing pair: found under exhaustive search? with what E-value and bits?
awk -F'\t' 'NR==FNR {e[$1 "\t" $2]=$13 "\t" $14; next}
  { p=$2 "\t" $3; printf "%s\t%s\t%s\n", $1, p, (p in e) ? "found\t" e[p] : "absent\t-\t-" }' \
  "$O/exhaustive.tsv" /tmp/pairs.tsv | tee "$O/pairs_exhaustive.tsv"
awk -F'\t' '{n[$1 " " $4]++} END{for (k in n) printf "%s\t%d\n", k, n[k]}' "$O/pairs_exhaustive.tsv" | sort | tee -a "$O/diag.tsv"
echo "DIAG DONE"
