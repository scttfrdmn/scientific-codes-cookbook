#!/usr/bin/env bash
# Task 03 (VEP image): VEP in offline GFF mode on the same sites, GFF3 and reference, then the
# three-way comparison against snpEff and bcftools csq and against Ensembl's published peptides.
#   bash vep.sh <forks>
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
F=${1:?forks}

machine
VV=$(vep 2>&1 | awk '/ensembl-vep *:/{print $3; exit}')
rec vep_version "$VV"
rec vep_ensembl_version "$(vep 2>&1 | awk '/ensembl  *:/{print $3; exit}')"
rec perl_version "$(perl -e 'print $^V')"
rec forks "$F"
hwm_start perl vep

echo "== GFF3 for VEP: position-sorted, bgzipped, tabix-indexed =="
TAB="$(printf '\t')"
make_gff "$W/genes.gff3"
grep -v '^#' "$W/genes.gff3" | sort -t "$TAB" -k1,1 -k4,4n -k5,5n | bgzip -c > "$W/genes.vep.gff3.gz"
tabix -p gff "$W/genes.vep.gff3.gz" || { echo "FAIL: tabix"; exit 1; }
cp "$REF" "$W/ref.fa"

echo "== VEP =="
tic
vep -i sites.vcf.gz --format vcf --vcf --gff "$W/genes.vep.gff3.gz" --fasta "$W/ref.fa" --species custom \
    --fork "$F" --compress_output bgzip -o "$O/vep.vcf.gz" --stats_text --stats_file "$O/vep.stats.txt" \
    --force_overwrite > "$O/vep.log" 2>&1
RC=$?
toc vep
chk "VEP exit status" "$RC" 0
tail -5 "$O/vep.log"
[ -f "$O/vep.vcf.gz_warnings.txt" ] && rec vep_warning_lines "$(wc -l < "$O/vep.vcf.gz_warnings.txt")"
gzip -dc "$O/vep.vcf.gz" | awk -v tool=vep -v sum="$O/vep.pairs.summary" -f pairs.awk | sort > "$O/vep.pairs.tsv"
cat "$O/vep.pairs.summary"
NS=$(gzip -dc sites.vcf.gz | awk '!/^#/{n++} END{print n}')
VN=$(awk -F'\t' '$1=="records"{print $2}' "$O/vep.pairs.summary")
chk "VEP records out == sites in" "$VN" "$NS"
chk "VEP record IDs in order == sites (md5 of the ID column)" \
  "$(gzip -dc "$O/vep.vcf.gz" | awk -F'\t' '!/^#/{print $3}' | md5)" "$(gzip -dc sites.vcf.gz | awk -F'\t' '!/^#/{print $3}' | md5)"
grep -E '^(Lines of input read|Variants processed|Variants filtered out|Novel / existing variants|Overlapped genes|Overlapped transcripts)' "$O/vep.stats.txt" | tee "$O/vep.stats.counts.txt"
VP=$(awk -F'\t' '$1=="Variants processed"{print $2}' "$O/vep.stats.txt")
rec vep_variants_processed "$VP"
chk "VEP's own 'Variants processed' == sites in" "$VP" "$NS"
rec vep_records_annotated "$(awk -F'\t' '$1=="records_annotated"{print $2}' "$O/vep.pairs.summary")"
rec vep_pairs "$(awk -F'\t' '$1=="pairs"{print $2}' "$O/vep.pairs.summary")"
rec vep_text_md5 "$(gzip -dc "$O/vep.vcf.gz" | grep -v '^##' | md5)"
rec vep_pairs_md5 "$(md5 < "$O/vep.pairs.tsv")"
hwm_stop

echo "== three annotators, one set of sites, one GFF3, one reference =="
gzip -dc Homo_sapiens.GRCh37.pep.all.fa.gz | awk '
  /^>/{ if (id != "") print id "\t" s; id = ""; s = ""
        if ($3 ~ /^chromosome:GRCh37:(20|21|22):/) for (i = 4; i <= NF; i++) if ($i ~ /^transcript:/) { id = substr($i, 12); sub(/\.[0-9]+$/, "", id) }
        next }
  id != "" { s = s $0 }
  END { if (id != "") print id "\t" s }' > "$W/pep.tsv"
rec ensembl_peptides_chr20_22 "$(wc -l < "$W/pep.tsv")"
: > "$O/disagree.tsv"; : > "$O/pep_mismatch.tsv"
tic
awk -v out="$O" -f compare.awk "$W/pep.tsv" snpeff.pairs.tsv csq.pairs.tsv "$O/vep.pairs.tsv" | sort > "$O/compare.tsv"
toc compare
cat "$O/compare.tsv"
sort -o "$O/disagree.tsv" "$O/disagree.tsv"; sort -o "$O/pep_mismatch.tsv" "$O/pep_mismatch.tsv"
awk -F'\t' '$NF != "x5"' "$O/disagree.tsv" | head -40
awk -F'\t' '$NF == "unexplained"' "$O/pep_mismatch.tsv" | head -40
cv(){ awk -F'\t' -v k="$1" '$1==k{print $2}' "$O/compare.tsv"; }
chk "pairs annotated by all three > 100000 (a real run)" "$( [ "$(cv pairs_in_all_three)" -gt 100000 ] && echo yes)" yes
chk "csq annotates exactly the pairs all three do" "$(cv pairs_csq)" "$(cv pairs_in_all_three)"
chk "VEP annotates exactly the pairs all three do" "$(cv pairs_vep)" "$(cv pairs_in_all_three)"
chk "snpEff's extra pairs == those naming no amino acid" "$(( $(cv pairs_snpeff) - $(cv pairs_in_all_three) ))" "$(cv snpeff_only_pairs_without_amino_acids)"
chk "no pair is in exactly two annotators" "$(( $(cv pairs_in_snpeff+csq) + $(cv pairs_in_snpeff+vep) + $(cv pairs_in_csq+vep) ))" 0
for t in snpeff csq vep; do
  chk "$t: no duplicate (record, transcript) pairs" "$(cv duplicate_pairs_$t)" 0
  chk "$t: every ref residue == Ensembl's peptide, but for x5 transcripts and selenocysteine" "$(cv pep_ref_mismatch_unexplained_$t)" 0
done
chk "VEP numbers residues as Ensembl does, x5 transcripts included" "$(cv pep_ref_mismatch_x5_vep)" 0
chk "every three-way disagreement is x5, codon 1 or an in-CDS stop" "$(cv all_three_disagree_unexplained)" 0
chk "three-way agreement + explained disagreements == pairs all three annotate" \
  "$(( $(cv all_three_agree_exact_classes) + $(cv all_three_agree_other_classes) + $(cv all_three_disagree_x5) + $(cv all_three_disagree_codon1) + $(cv all_three_disagree_stop) ))" "$(cv pairs_in_all_three)"
# what VEP said for the pairs only snpEff gives a protein-level consequence (observation)
comm -23 <(cut -f1,2 snpeff.pairs.tsv) <(cut -f1,2 "$O/vep.pairs.tsv") > "$W/snpeff.only"
gzip -dc "$O/vep.vcf.gz" | awk -F'\t' -v csqf="$(gzip -dc "$O/vep.vcf.gz" | awk '/^##INFO=<ID=CSQ,/{print; exit}')" '
  BEGIN { s = csqf; sub(/.*Format: /, "", s); sub(/">.*/, "", s); n = split(s, h, "|"); for (i = 1; i <= n; i++) c[h[i]] = i }
  FNR == NR { want[$1 "\t" $2] = 1; ids[$1] = 1; next }
  !/^#/ && ($3 in ids) { m = split($8, kv, ";"); for (i = 1; i <= m; i++) if (index(kv[i], "CSQ=") == 1) {
      q = split(substr(kv[i], 5), e, ","); for (j = 1; j <= q; j++) { split(e[j], g, "|"); t = g[c["Feature"]]; sub(/\.[0-9]+$/, "", t)
        if (($3 "\t" t) in want) print $3, t, g[c["Consequence"]], g[c["Protein_position"]], g[c["Amino_acids"]] } } }' OFS='\t' "$W/snpeff.only" - \
  | sort | tee "$O/snpeff.only.vep.tsv" | cut -f3 | sort | uniq -c
finish "VEP"
