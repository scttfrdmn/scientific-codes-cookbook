#!/usr/bin/env bash
# Task 02 (snpEff image): build a snpEff database from the same Ensembl GFF3 and reference that
# bcftools csq read, with Ensembl's CDS and peptide FASTA as the build's own check, then annotate
# the same sites.vcf.gz and write the per-transcript pairs.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
G=GRCh37e87
X=-Xmx6g

machine
rec snpeff_version "$(snpEff -version 2>&1 | head -1)"
rec java_version "$(java -version 2>&1 | head -1)"
rec jvm_heap "$X"
hwm_start java

echo "== database: GFF3 + reference + Ensembl CDS/peptides for chr20-22 =="
make_gff "$W/genes.gff3"
# snpEff keys transcripts by the GFF3 ID, which keeps Ensembl's prefix (ID=transcript:ENST...):
# rename each FASTA record to that key; sequences are copied unchanged.
gzip -dc Homo_sapiens.GRCh37.cds.all.fa.gz | awk '
  /^>/{ p = ($3 ~ /^chromosome:GRCh37:(20|21|22):/); if (p) { id = substr($1, 2); sub(/\.[0-9]+$/, "", id); print ">transcript:" id }; next } p' > "$W/cds.fa"
gzip -dc Homo_sapiens.GRCh37.pep.all.fa.gz | awk '
  /^>/{ p = ($3 ~ /^chromosome:GRCh37:(20|21|22):/); if (p) { id = ""; for (i = 4; i <= NF; i++) if ($i ~ /^transcript:/) id = substr($i, 12)
        sub(/\.[0-9]+$/, "", id); print ">transcript:" id }; next } p' > "$W/protein.fa"
rec ensembl_cds_records "$(grep -c '^>' "$W/cds.fa")"
rec ensembl_protein_records "$(grep -c '^>' "$W/protein.fa")"
# Ensembl names transcript features by kind (mRNA, NMD_transcript_variant, processed_transcript,
# lincRNA, ...) and genes likewise (gene, ncRNA_gene, pseudogene). The database is built twice:
# from the GFF3 as published (`raw`, build log kept for comparison) and with the type column of
# every ID=transcript: / ID=gene: line renamed to mRNA / gene -- coordinates, IDs and attributes
# (biotype= among them) untouched. The renamed one is annotated with; the tally of renamed types
# is kept in gff.types.tsv.
awk -F'\t' 'BEGIN{OFS="\t"} /^#/{print; next}
  { a = ";" $9 }
  a ~ /;ID=transcript:/ { t["transcript\t" $3]++; $3 = "mRNA" }
  a ~ /;ID=gene:/       { t["gene\t" $3]++; $3 = "gene" }
  { print }
  END { for (k in t) print k "\t" t[k] > "/tmp/out/gff.types.tsv" }' "$W/genes.gff3" > "$W/genes.renamed.gff3"
sort -o "$O/gff.types.tsv" "$O/gff.types.tsv"; cat "$O/gff.types.tsv"
printf 'data.dir = %s/data\n%s.genome : Homo_sapiens GRCh37.87 chr20-22\nraw.genome : as published\n' "$W" "$G" > "$W/snpEff.config"
field(){ # log, check name, key   e.g. "<TAB>CDS check:<TAB>genome<TAB>OK: n<TAB>Warnings: n<TAB>Not found: n<TAB>Errors: n..."
  grep "^$(printf '\t')$2" "$1" | head -1 | tr '\t' '\n' | awk -v k="$3" 'index($0, k) == 1 { sub(k, ""); gsub(/[^0-9.]/, ""); print; exit }'; }
build(){ # genome, gff, log, tag
  mkdir -p "$W/data/$1"
  cp "$2" "$W/data/$1/genes.gff"; cp "$REF" "$W/data/$1/sequences.fa"
  cp "$W/cds.fa" "$W/data/$1/cds.fa"; cp "$W/protein.fa" "$W/data/$1/protein.fa"
  tic
  snpEff $X build -c "$W/snpEff.config" -gff3 -v "$1" > "$3" 2>&1
  RC=$?
  toc "build_$4"
  chk "snpEff build ($4) exit status" "$RC" 0
  for c in CDS Protein; do
    for k in "OK: " "Warnings: " "Not found: " "Errors: "; do
      rec "build_${4}_$(echo "$c" | tr 'A-Z' 'a-z')_check_$(echo "$k" | tr -d ': ' | tr 'A-Z' 'a-z')" "$(field "$3" "$c check" "$k")"
    done
  done
  rec "build_${4}_transcripts_with_deleted_exons" "$(awk -F': ' '/Total transcripts with deleted exons/{print $2+0; exit}' "$3")"
  grep -o '^WARNING_[A-Z_]*' "$3" | sort | uniq -c > "$3.warnings.txt"; cat "$3.warnings.txt"
}
build raw "$W/genes.gff3" "$O/build.raw.log" raw
build "$G" "$W/genes.renamed.gff3" "$O/build.log" renamed
rm -rf "$W/data/raw"
P_OK=$(field "$O/build.log" "Protein check" "OK: "); P_ERR=$(field "$O/build.log" "Protein check" "Errors: ")
P_NF=$(field "$O/build.log" "Protein check" "Not found: ")
# snpEff counts transcript objects, not peptides, so OK is an observation; the claims are the zeros:
# no transcript translates to anything but Ensembl's peptide, no coding transcript lacks one.
rec protein_check_ok_transcript_objects "$P_OK"
chk "protein check: transcripts whose translation != Ensembl's published peptide" "$P_ERR" 0
chk "protein check: protein-coding transcripts with no Ensembl peptide" "$P_NF" 0

echo "== annotate the sites =="
tic
snpEff $X ann -c "$W/snpEff.config" -hgvs1LetterAa -csvStats "$O/snpeff.stats.csv" -s "$O/snpeff_summary.html" \
  "$G" sites.vcf.gz 2> "$O/ann.log" | gzip -c > "$O/snpeff.vcf.gz"   # the snpEff image has no bgzip
RC=$?
toc ann
chk "snpEff ann exit status" "$RC" 0
gzip -dc "$O/snpeff.vcf.gz" | awk -v tool=snpeff -v sum="$O/snpeff.pairs.summary" -f pairs.awk | sort > "$O/snpeff.pairs.tsv"
cat "$O/snpeff.pairs.summary"
NS=$(gzip -dc sites.vcf.gz | awk '!/^#/{n++} END{print n}')
SN=$(awk -F'\t' '$1=="records"{print $2}' "$O/snpeff.pairs.summary")
chk "snpEff records out == sites in" "$SN" "$NS"
chk "snpEff record IDs in order == sites (md5 of the ID column)" \
  "$(gzip -dc "$O/snpeff.vcf.gz" | awk -F'\t' '!/^#/{print $3}' | md5)" "$(gzip -dc sites.vcf.gz | awk -F'\t' '!/^#/{print $3}' | md5)"
SANN=$(awk -F'\t' '$1=="records_annotated"{print $2}' "$O/snpeff.pairs.summary")
rec snpeff_records_annotated "$SANN"
# what the records without an ANN are, by REF/ALT shape (observation)
gzip -dc "$O/snpeff.vcf.gz" | awk -F'\t' '!/^#/ && $8 !~ /(^|;)ANN=/ {
    s = ($5 ~ /^</) ? "symbolic" : ($4 ~ /^[ACGT]$/ && $5 ~ /^[ACGT]$/) ? "snv" : (length($4) == length($5)) ? "mnp" : "indel"
    c[s]++; if (n++ < 20) print $1, $2, $3, $4, substr($5, 1, 40) > "/tmp/out/snpeff.noann.sample.txt" }
  END { for (k in c) print "noann_" k "\t" c[k] }' | sort | tee "$O/snpeff.noann.tsv"
NOANN_SYM=$(awk -F'\t' '$1=="noann_symbolic"{print $2}' "$O/snpeff.noann.tsv")
chk "every record without an ANN is a symbolic allele (records - annotated == symbolic unannotated)" "$(( NS - SANN ))" "${NOANN_SYM:-0}"
# snpEff's own tally of what it read
grep -i -E '^ *Number of (lines|variants)|Number_of' "$O/snpeff.stats.csv" | head -5 | tee "$O/snpeff.stats.counts.txt"
gzip -dc "$O/snpeff.vcf.gz" | awk -F'\t' '!/^#/{ n = split($8, kv, ";"); for (i = 1; i <= n; i++) if (index(kv[i], "ANN=") == 1) {
    m = split(kv[i], e, ","); for (j = 1; j <= m; j++) { k = split(e[j], f, "|"); if (f[16] != "") { q = split(f[16], w, "&"); for (z = 1; z <= q; z++) c[w[z]]++ } } } }
  END { for (x in c) print x "\t" c[x] }' | sort > "$O/snpeff.messages.tsv"
cat "$O/snpeff.messages.tsv"
rec snpeff_pairs "$(awk -F'\t' '$1=="pairs"{print $2}' "$O/snpeff.pairs.summary")"
rec snpeff_text_md5 "$(gzip -dc "$O/snpeff.vcf.gz" | grep -v '^##' | md5)"
rec snpeff_pairs_md5 "$(md5 < "$O/snpeff.pairs.tsv")"
hwm_stop
finish "SNPEFF"
