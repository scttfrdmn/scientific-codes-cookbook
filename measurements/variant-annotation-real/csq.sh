#!/usr/bin/env bash
# Task 01 (bcftools image): the sites every annotator reads, then the first annotator.
#   1000 Genomes Phase 3 chr20-22 -> drop genotypes -> split multiallelics + left-align against
#   hs37d5 -> concatenate -> number every record (v1..vN; the release repeats some records
#   verbatim, so neither rsID nor CHROM:POS:REF:ALT is a key) -> sites.vcf.gz
#   then `bcftools csq -l` against the Ensembl GRCh37.87 GFF3 and the per-transcript pairs.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
T=4
vcf(){ echo "ALL.chr$1.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"; }

machine
rec bcftools_version "$(bcftools --version | head -1)"
rec htslib_version "$(bcftools --version | sed -n 2p)"
rec threads "$T"
hwm_start bcftools

echo "== sites: genotypes dropped, multiallelics split, left-aligned =="
tic
N_IN=0; ALTS_IN=0
for C in $CHRS; do
  read -r n a < <(gzip -dc "$(vcf "$C")" | awk -F'\t' '!/^#/{n++; a+=split($5,x,",")} END{print n, a}')
  N_IN=$((N_IN + n)); ALTS_IN=$((ALTS_IN + a))
  bcftools view -G -Ou "$(vcf "$C")" | bcftools norm -m -any -f "$REF" -Ob -o "$W/chr$C.bcf" 2> "$O/norm.chr$C.log" \
    || { echo "FAIL: norm chr$C"; tail "$O/norm.chr$C.log"; exit 1; }
done
bcftools concat -Ov "$W/chr20.bcf" "$W/chr21.bcf" "$W/chr22.bcf" \
  | awk -F'\t' 'BEGIN{OFS="\t"} /^#/{print; next} {$3 = "v" (++n); print}' \
  | bgzip -@ "$T" -c > "$O/sites.vcf.gz"
toc sites
rec input_records "$N_IN"
rec input_alt_alleles "$ALTS_IN"
read -r NS NSNV NSYM < <(gzip -dc "$O/sites.vcf.gz" | awk -F'\t' '!/^#/{n++; if($4~/^[ACGT]$/ && $5~/^[ACGT]$/) s++; if($5~/^</) y++} END{print n, s, y+0}')
rec site_records "$NS"
rec site_snvs "$NSNV"
rec site_symbolic "$NSYM"
chk "split records == ALT alleles in the release" "$NS" "$ALTS_IN"
SITES_MD5=$(gzip -dc "$O/sites.vcf.gz" | grep -v '^##' | md5)
rec sites_text_md5 "$SITES_MD5"

echo "== bcftools csq, local (one record at a time) =="
make_gff "$W/genes.gff3"
bgzip -c "$W/genes.gff3" > "$W/genes.gff3.gz"
tic
bcftools csq -l -f "$REF" -g "$W/genes.gff3.gz" --threads "$T" -Oz -o "$O/csq.vcf.gz" "$O/sites.vcf.gz" 2> "$O/csq.log"
RC=$?
toc csq
rec csq_rc "$RC"
chk "csq exit status" "$RC" 0
grep -c -i warn "$O/csq.log" | { read -r w; rec csq_log_warnings "$w"; }
gzip -dc "$O/csq.vcf.gz" | awk -v tool=csq -v sum="$O/csq.pairs.summary" -f pairs.awk | sort > "$O/csq.pairs.tsv"
cat "$O/csq.pairs.summary"
CN=$(awk -F'\t' '$1=="records"{print $2}' "$O/csq.pairs.summary")
chk "csq records out == sites in" "$CN" "$NS"
chk "csq record IDs in order == sites (md5 of the ID column)" \
  "$(gzip -dc "$O/csq.vcf.gz" | awk -F'\t' '!/^#/{print $3}' | md5)" "$(gzip -dc "$O/sites.vcf.gz" | awk -F'\t' '!/^#/{print $3}' | md5)"
rec csq_records_annotated "$(awk -F'\t' '$1=="records_annotated"{print $2}' "$O/csq.pairs.summary")"
rec csq_pairs "$(awk -F'\t' '$1=="pairs"{print $2}' "$O/csq.pairs.summary")"
rec csq_text_md5 "$(gzip -dc "$O/csq.vcf.gz" | grep -v '^##' | md5)"
rec csq_pairs_md5 "$(md5 < "$O/csq.pairs.tsv")"
hwm_stop
finish "CSQ"
