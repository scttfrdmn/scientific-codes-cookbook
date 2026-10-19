#!/usr/bin/env bash
# Task 01 (minimac4 image, which ships bcftools): split 1000 Genomes Phase 3 chr20 into a reference
# panel and a held-out target cohort, mask the target down to an array, and compress the panel.
#
#   samples   every 10th of the 2,504 in file order is a target (250), the other 2,254 the panel
#   sites     biallelic SNPs, positions occurring once (a split multiallelic or a repeated record
#             is dropped whole), polymorphic in the panel (--min-ac 1:minor)
#   array     the panel sites that are also on the Illumina Omni2.5 (same CHROM/POS/REF/ALT)
#   truth     the 250 targets' own phased genotypes at every panel site
#   target    the truth at the array sites only, phase removed -- what a genotyped cohort hands in
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
machine
rec bcftools_version "$(bcftools --version | head -1)"
rec minimac4_version "$(minimac4 --version 2>&1 | head -1)"
rec threads "$T"
hwm_start bcftools minimac4

echo "== samples =="
bcftools query -l "$VCF" > "$W/all.samples"
awk 'NR%10==0' "$W/all.samples" > "$O/target.samples"
awk 'NR%10!=0' "$W/all.samples" > "$O/ref.samples"
rec samples_total "$(wc -l < "$W/all.samples" | tr -d ' ')"
rec samples_target "$(wc -l < "$O/target.samples" | tr -d ' ')"
rec samples_ref "$(wc -l < "$O/ref.samples" | tr -d ' ')"
chk "target and panel share no sample" "$(sort "$O/target.samples" "$O/ref.samples" | uniq -d | wc -l | tr -d ' ')" 0

echo "== biallelic SNPs at unique positions =="
tic
bcftools view --threads 4 -m2 -M2 -v snps -Ob -o "$W/snps.bcf" "$VCF" 2> "$O/view.log" || { echo "FAIL: view"; tail "$O/view.log"; exit 1; }
bcftools index "$W/snps.bcf"
bcftools query -f '%CHROM\t%POS\n' "$W/snps.bcf" | uniq -d > "$W/dup.pos"
rec biallelic_snp_records "$(bcftools index -n "$W/snps.bcf")"
rec dup_positions_dropped "$(wc -l < "$W/dup.pos" | tr -d ' ')"
toc snps

echo "== panel: 2,254 samples, polymorphic sites, INFO reduced to AC/AN =="
tic
EXCL=""; [ -s "$W/dup.pos" ] && EXCL="-T ^$W/dup.pos"
bcftools view --threads 4 $EXCL -S "$O/ref.samples" --min-ac 1:minor -Ou "$W/snps.bcf" \
  | bcftools annotate -x INFO,^FORMAT/GT -Ou \
  | bcftools +fill-tags -Oz -o "$O/ref.vcf.gz" -- -t AN,AC 2> "$O/ref.log"
tabix -p vcf "$O/ref.vcf.gz"
bcftools query -f '%CHROM:%POS:%REF:%ALT\t%AC\t%AN\n' "$O/ref.vcf.gz" > "$O/ref.sites.tsv"
bcftools query -f '%CHROM\t%POS\n' "$O/ref.vcf.gz" > "$W/ref.pos"
NREF=$(wc -l < "$O/ref.sites.tsv" | tr -d ' ')
rec panel_sites "$NREF"
chk "panel AN == 2 x 2,254 at every site" "$(awk -F'\t' '$3!=4508' "$O/ref.sites.tsv" | wc -l | tr -d ' ')" 0
chk "every panel site polymorphic in the panel (0 < AC < AN)" "$(awk -F'\t' '$2<1 || $2>=$3' "$O/ref.sites.tsv" | wc -l | tr -d ' ')" 0
chk "panel positions unique" "$(uniq -d "$W/ref.pos" | wc -l | tr -d ' ')" 0
toc panel

echo "== truth: the 250 targets at every panel site =="
tic
bcftools view --threads 4 -T "$W/ref.pos" -S "$O/target.samples" -I -Ou "$W/snps.bcf" \
  | bcftools annotate -x INFO,^FORMAT/GT -Oz -o "$O/truth.vcf.gz" 2> "$O/truth.log"
tabix -p vcf "$O/truth.vcf.gz"
chk "truth sites == panel sites, in order" \
  "$(bcftools query -f '%CHROM:%POS:%REF:%ALT\n' "$O/truth.vcf.gz" | md5)" "$(cut -f1 "$O/ref.sites.tsv" | md5)"
toc truth

echo "== array: panel sites on the Omni2.5, exact allele match =="
awk -F'\t' 'BEGIN{OFS="\t"} NR==FNR{ a[$1":"$2":"$3":"$4]=1; s[$1":"$2":"$4":"$3]=1; next }
  { split($1,k,":"); if ($1 in a) { print k[1], k[2] > "'"$O"'/typed.pos"; n++ } else if ($1 in s) w++ }
  END{ printf "typed\t%d\nswapped_alleles_dropped\t%d\n", n, w+0 }' omni.chr20.sites.tsv "$O/ref.sites.tsv" > "$W/typed.count"
cat "$W/typed.count"
NTYPED=$(awk '$1=="typed"{print $2}' "$W/typed.count")
rec omni_chr20_sites "$(wc -l < omni.chr20.sites.tsv | tr -d ' ')"
rec typed_sites "$NTYPED"
rec typed_swapped_dropped "$(awk '$1=="swapped_alleles_dropped"{print $2}' "$W/typed.count")"
rec masked_sites "$((NREF - NTYPED))"

echo "== target: truth at the array sites, unphased =="
tic
bcftools view -T "$O/typed.pos" -Ou "$O/truth.vcf.gz" \
  | bcftools +setGT -Oz -o "$O/target.vcf.gz" -- -t a -n u 2> "$O/target.log"
tabix -p vcf "$O/target.vcf.gz"
chk "target sites == array sites" "$(bcftools index -n "$O/target.vcf.gz")" "$NTYPED"
chk "target genotypes all unphased" \
  "$(bcftools query -f '[%GT\n]' "$O/target.vcf.gz" | grep -c '|' | tr -d ' ')" 0
toc target

echo "== minimac4: compress the panel =="
tic
minimac4 --compress-reference "$O/ref.vcf.gz" -o "$O/ref.msav" --threads "$T" > "$O/compress.log" 2>&1
RC=$?
toc compress
chk "minimac4 --compress-reference exit status" "$RC" 0
rec ref_vcf_gz_bytes "$(wc -c < "$O/ref.vcf.gz" | tr -d ' ')"
rec ref_msav_bytes "$(wc -c < "$O/ref.msav" | tr -d ' ')"

rec ref_body_md5 "$(body_md5 "$O/ref.vcf.gz")"
rec truth_body_md5 "$(body_md5 "$O/truth.vcf.gz")"
rec target_body_md5 "$(body_md5 "$O/target.vcf.gz")"
rec ref_msav_md5 "$(md5 < "$O/ref.msav")"
hwm_stop
finish "PREP"
