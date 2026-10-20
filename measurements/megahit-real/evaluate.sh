#!/usr/bin/env bash
# megahit-real, task 2 (QUAST image): score every chip's assembly of one sample against Zymo's
# reference genomes in one metaQUAST run, and assert the same claims of each.
#
#   bash evaluate.sh <Zymo reference zip> <accession> <threads> <organisms to recover,...> <label> [<label> ...]
#
# Needs /tmp/<accession>.<label>.final.contigs.fa and .megahit.log for each label (a chip, its
# repeat, or a -t 1 run). Writes /tmp/out/<accession>/.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
ZIP=$1 ACC=$2 T=$3 RECOVER=$4; shift 4
CHIPS="$*"
O=/tmp/out/$ACC
mkdir -p "$O"
cd /tmp
FAILS=0
row() { printf '%-8s %-58s %-22s %s\n' "$1" "$2" "$3" "$4" | tee -a "$O/checks.txt"; }
check() { if [ "$1" = 1 ]; then row "$2" "$3" "$4" "ok   ($5)"; else row "$2" "$3" "$4" "FAIL ($5)"; FAILS=$((FAILS+1)); fi; }

quast.py --version 2>&1 | head -1 | tee "$O/versions.txt"
if [ ! -d refs ]; then
  python3 -u -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall("refs")' "$ZIP"
  for f in refs/ZymoBIOMICS.STD.refseq.v2/Genomes/*.fasta; do
    s=$(basename "$f" .fasta); s=${s%_complete_genome}; s=${s%_draft_genome}
    cp "$f" "refs/$s.fasta"
  done
fi
cat refs/ZymoBIOMICS.STD.refseq.v2/Version | tee -a "$O/versions.txt"
REFS=""
for f in refs/*.fasta; do
  REFS="${REFS:+$REFS,}$f"
  awk -v s="$(basename "$f" .fasta)" '/^>/{n++; next}{l+=length($0)} END{printf "%s sequences %d bp %d\n", s, n, l}' "$f" >> "$O/references.txt"
done
cat "$O/references.txt"

FILES="" LABELS=""
for c in $CHIPS; do FILES="$FILES $ACC.$c.final.contigs.fa"; LABELS="${LABELS:+$LABELS,}$c"; done
T0=$(date +%s)
# shellcheck disable=SC2086
metaquast.py $FILES -r "$REFS" -t "$T" -o "mq-$ACC" --labels "$LABELS" --no-icarus --no-plots > "$O/metaquast.log" 2>&1
RC=$?
echo "metaquast_rc $RC wall_s $(( $(date +%s) - T0 ))" | tee "$O/times.txt"
mkdir -p "$O/summary"
cp mq-$ACC/summary/TSV/*.tsv "$O/summary/" 2>/dev/null
cp mq-$ACC/combined_reference/report.tsv "$O/combined_report.tsv" 2>/dev/null
cp mq-$ACC/combined_reference/contigs_reports/misassemblies_report.tsv "$O/" 2>/dev/null
cp mq-$ACC/combined_reference/contigs_reports/unaligned_report.tsv "$O/" 2>/dev/null
cp -r mq-$ACC/not_aligned "$O/" 2>/dev/null
check "$([ "$RC" = 0 ] && [ -s "$O/combined_report.tsv" ] && echo 1)" all "metaquast.py exit, combined report written" "want 0" "$RC"

# Claims against Zymo's truth, of every assembly. Column of a label = its position in CHIPS + 1.
#  - recovered: each organism in RECOVER (abundant enough to assemble: 12% each in D6300; the top
#    three decades in D6310) has >= 90% of its reference covered. A dropped or collapsed genome
#    misses this by tens of points; a correct assembly at this depth sits near 99%.
#  - consensus: <= 50 mismatches per 100 kbp against that organism's own reference. These are the
#    standard's own strains, so a correct consensus differs only by repeats and stock drift.
#  - closed community: fully unaligned contigs (QUAST counts contigs >= 500 bp) total <= 0.5% of
#    the assembly. One off-community genome at assemblable depth would be megabases, i.e. several %.
tsv() { awk -F'\t' -v k="$1" -v c="$2" '$1==k{print $c}' "$3"; }
i=1
for c in $CHIPS; do
  i=$((i+1))
  for org in $(echo "$RECOVER" | tr ',' ' '); do
    GF=$(tsv "$org" $i "$O/summary/Genome_fraction.tsv"); MM=$(tsv "$org" $i "$O/summary/num_mismatches_per_100_kbp.tsv")
    check "$(awk -v g="$GF" 'BEGIN{print (g ~ /^[0-9.]+$/ && g+0>=90.0)?1:0}')" "$c" "$org recovered: genome fraction >= 90%" "want >= 90.0" "$GF"
    check "$(awk -v m="$MM" 'BEGIN{print (m ~ /^[0-9.]+$/ && m+0<=50)?1:0}')" "$c" "$org consensus: mismatches per 100 kbp <= 50" "want <= 50" "$MM"
  done
  FU=$(tsv 'Fully unaligned length' $i "$O/unaligned_report.tsv"); TL=$(tsv 'Total length' $i "$O/combined_report.tsv")
  check "$(awk -v u="$FU" -v t="$TL" 'BEGIN{print (u ~ /^[0-9]+$/ && t+0>0 && u/t<=0.005)?1:0}')" "$c" "closed community: fully unaligned <= 0.5% of assembly" "want <= 0.005" "$FU / $TL"
done

echo "== cross-chip: assembly bytes ==" | tee "$O/compare.txt"
for c in $CHIPS; do sha256sum "$ACC.$c.final.contigs.fa"; done | tee -a "$O/compare.txt"
N=$(for c in $CHIPS; do sha256sum "$ACC.$c.final.contigs.fa" | awk '{print $1}'; done | sort -u | wc -l)
echo "distinct_files $N of $(echo $CHIPS | wc -w)" | tee -a "$O/compare.txt"
for c in $CHIPS; do python3 -u canon.py "$ACC.$c.final.contigs.fa" | sed "s/^/$c /"; done | tee -a "$O/compare.txt"
NS=$(awk '$2=="contig_set_sha256"{print $3}' "$O/compare.txt" | sort -u | wc -l)
NL=$(awk '$2=="length_multiset_sha256"{print $3}' "$O/compare.txt" | sort -u | wc -l)
echo "distinct_contig_sets $NS distinct_length_multisets $NL" | tee -a "$O/compare.txt"
set -- $CHIPS
FIRST=$1
for c in $CHIPS; do
  [ "$c" = "$FIRST" ] && continue
  python3 -u compare.py "$FIRST" "$ACC.$FIRST.final.contigs.fa" "$c" "$ACC.$c.final.contigs.fa" | tee -a "$O/compare.txt"
done
pair() { [ -e "$ACC.$1.final.contigs.fa" ] && [ -e "$ACC.$2.final.contigs.fa" ] && \
  python3 -u compare.py "$1" "$ACC.$1.final.contigs.fa" "$2" "$ACC.$2.final.contigs.fa" | tee -a "$O/compare.txt"; }
pair x86_64 amd
pair arm64-t1 x86_64-t1
pair arm64 arm64-t1
pair x86_64 x86_64-t1
# At -t 1 nothing is scheduled, so one build must give one file: Intel, AMD, and AMD with MEGAHIT's
# portable core forced (--no-hw-accel, the core aarch64 runs) are the same x86 binary package.
X1=""
for c in x86_64-t1 amd-t1 amd-nohw-t1; do [ -e "$ACC.$c.final.contigs.fa" ] && X1="$X1 $c"; done
if [ -n "$X1" ]; then
  NX=$(for c in $X1; do sha256sum "$ACC.$c.final.contigs.fa" | awk '{print $1}'; done | sort -u | wc -l)
  check "$([ "$NX" = 1 ] && [ "$(echo $X1 | wc -w)" = 3 ] && echo 1)" all "-t 1, x86 build: Intel == AMD == AMD --no-hw-accel, byte for byte" "want 1 distinct of 3" "$NX of$X1"
fi
for c in $CHIPS; do
  check "$(grep -q 'ALL DONE' "$ACC.$c.megahit.log" && echo 1)" "$c" "completion sentinel in megahit log" "'ALL DONE'" "$ACC.$c.megahit.log"
done
echo "evaluate_failures $FAILS" | tee -a "$O/checks.txt"
[ "$FAILS" = 0 ] || exit 1
echo "EVALUATE OK $ACC"
