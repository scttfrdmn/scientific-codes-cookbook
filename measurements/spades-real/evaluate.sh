#!/usr/bin/env bash
# spades-real, task 2 (QUAST image): score every chip's assembly against the closed reference in one
# QUAST run, and assert the same claims of each.
#
#   bash evaluate.sh <reference.fna.gz> <threads> <chip> [<chip> ...]
#
# Needs /tmp/<chip>.contigs.fasta and /tmp/<chip>.spades.log for each chip, and /tmp/pairs.txt
# (read pairs in the staged run, from prep.sh). Writes /tmp/out/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
REF=$1 T=$2; shift 2
CHIPS="$*"
O=/tmp/out
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-8s %-46s %-24s %s\n' "$1" "$2" "$3" "$4" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "$4" "ok   ($5)"; else row "$2" "$3" "$4" "FAIL ($5)"; FAILS=$((FAILS+1)); fi; }
PAIRS=$(cat pairs.txt)

quast.py --version 2>&1 | head -1 | tee "$O/versions.txt"
FILES="" LABELS=""
for c in $CHIPS; do FILES="$FILES $c.contigs.fasta"; LABELS="${LABELS:+$LABELS,}$c"; done
T0=$(date +%s)
# shellcheck disable=SC2086
quast.py $FILES -r "$REF" -t "$T" -o q --labels "$LABELS" > "$O/quast.log" 2>&1
RC=$?
echo "quast_rc $RC wall_s $(( $(date +%s) - T0 ))" | tee "$O/times.txt"
cp q/report.tsv q/report.txt "$O/" 2>/dev/null
cp q/contigs_reports/misassemblies_report.tsv "$O/" 2>/dev/null
check "$([ "$RC" = 0 ] && [ -s "$O/report.tsv" ] && echo 1)" all "quast.py exit, report.tsv written" "want 0" "$RC"

# report.tsv: metric<TAB>chip1<TAB>chip2...; column of chip = position in CHIPS + 1
q() { awk -F'\t' -v k="$1" -v c="$2" '$1==k{print $c}' "$O/report.tsv"; }
i=1
for c in $CHIPS; do
  i=$((i+1))
  GF=$(q 'Genome fraction (%)' $i); MM=$(q '# mismatches per 100 kbp' $i); ID=$(q '# indels per 100 kbp' $i)
  TL=$(q 'Total length' $i); RL=$(q 'Reference length' $i); MIS=$(q '# misassemblies' $i)
  UL=$(q 'Unaligned length' $i); NGA=$(q 'NGA50' $i); LA=$(q 'Largest alignment' $i); DR=$(q 'Duplication ratio' $i)
  echo "$c genome_fraction $GF mismatches_per_100kbp $MM indels_per_100kbp $ID total_length $TL reference_length $RL misassemblies $MIS unaligned_length $UL nga50 $NGA largest_alignment $LA duplication_ratio $DR" | tee -a "$O/metrics.txt"
  check "$(awk -v g="$GF" 'BEGIN{print (g+0>=95.0)?1:0}')" "$c" "genome fraction >= 95% of U00096.3" "want >= 95.0" "$GF"
  check "$(awk -v m="$MM" 'BEGIN{print (m!="" && m+0<=10)?1:0}')" "$c" "mismatches per 100 kbp <= 10" "want <= 10" "$MM"
  check "$(awk -v m="$ID" 'BEGIN{print (m!="" && m+0<=10)?1:0}')" "$c" "indels per 100 kbp <= 10" "want <= 10" "$ID"
  check "$(awk -v t="$TL" -v r="$RL" 'BEGIN{print (r>0 && t/r>=0.95 && t/r<=1.05)?1:0}')" "$c" "total length within 5% of the reference" "want 0.95-1.05" "$TL / $RL"
  check "$(grep -q 'SPAdes pipeline finished' "$c.spades.log" && echo 1)" "$c" "completion sentinel in spades.log" "grep" "$c.spades.log"
done

echo "== cross-chip: assembly bytes ==" | tee "$O/compare.txt"
for c in $CHIPS; do sha256sum "$c.contigs.fasta"; done | tee -a "$O/compare.txt"
N=$(for c in $CHIPS; do sha256sum "$c.contigs.fasta" | awk '{print $1}'; done | sort -u | wc -l)
echo "distinct_assemblies $N of $(echo $CHIPS | wc -w)" | tee -a "$O/compare.txt"
# same reads, same -t 8, same version: an assembly is an integer graph computation, so the chip
# must not change a byte of it
check "$([ "$N" = 1 ] && echo 1)" all "contigs.fasta byte-identical on every chip" "want 1 distinct" "$N"
echo "evaluate_failures $FAILS" | tee -a "$O/checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "EVALUATE OK"
