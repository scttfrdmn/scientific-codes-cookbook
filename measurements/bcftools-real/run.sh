#!/usr/bin/env bash
# bcftools on a real cohort: 1000 Genomes Phase 3, chromosomes 20-22, all 2,504 samples.
#
# The typical VCF-processing day, end to end: normalise (split multiallelics + left-align
# against the reference), recompute and check the published tags, stats, filter to common
# SNPs, annotate from a sites file, concat across chromosomes, and split/merge across samples.
# Every check is an identity -- a count that must reconcile, a published value that must be
# reproduced, or a round trip that must be lossless -- never a band on an observed value.
#
# Runs in /tmp (the spawn staging tmpfs). Writes results.tsv (key<TAB>value), checks.tsv
# (claim<TAB>value<TAB>ok|FAIL), timings.tsv (step<TAB>seconds) and prints BCFTOOLS REAL OK
# only if every check passed. Never rm's a staged input; only files it created.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH
T=${THREADS:-4}
CHRS=${CHRS:-"20 21 22"}
MERGE_CHR=${MERGE_CHR:-22}
REF=hs37d5.chr20-22.fa
vcf(){ echo "ALL.chr$1.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"; }

: > results.tsv; : > checks.tsv; : > timings.tsv
FAILS=0
rec(){ printf '%s\t%s\n' "$1" "$2" >> results.tsv; echo "$1 = $2"; }
EMPTY_MD5=d41d8cd98f00b204e9800998ecf8427e   # md5 of no bytes: two empty streams must never "agree"
chk(){ # claim, lhs, rhs  -- exact equality of two non-empty strings
  if [ "$2" = "$3" ] && [ -n "$2" ] && [ "$2" != "$EMPTY_MD5" ]; then s=ok; else s=FAIL; FAILS=$((FAILS+1)); fi
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$s" >> checks.tsv; echo "CHECK [$s] $1: $2 vs $3"; }
tic(){ T0=$(date +%s); }
toc(){ d=$(( $(date +%s) - T0 )); printf '%s\t%s\n' "$1" "$d" >> timings.tsv; echo "TIME $1 ${d}s"; }

# peak RSS of any bcftools process: sample VmHWM once a second (no /usr/bin/time in either image)
( while :; do
    for f in /proc/[0-9]*/status; do
      awk '/^Name:/{n=$2} /^VmHWM:/{if(n=="bcftools")print $2}' "$f" 2>/dev/null
    done | awk 'BEGIN{m=0} $1>m{m=$1} END{print m}' >> rss.samples
    sleep 1
  done ) &
SAMPLER=$!

echo "== versions =="
bcftools --version | head -2
uname -m
rec arch "$(uname -m)"
rec bcftools_version "$(bcftools --version | head -1)"
rec htslib_version "$(bcftools --version | sed -n 2p)"
rec threads "$T"

# super-population sample lists from the published panel (fill-tags -S format: sample<TAB>group)
awk 'NR>1{print $1"\t"$3}' integrated_call_samples_v3.20130502.ALL.panel > pops.txt

# Census of a VCF from the text, independent of bcftools' own parsing:
# records, ALT alleles, multiallelic records, sum of every INFO/AC element, records with AN!=5008,
# and a classification of each single-ALT record the way `bcftools stats` defines it.
census(){
  gzip -dc "$1" | cut -f1-8 | awk -F'\t' '
    /^#/ {next}
    { n++; na=split($5,a,","); alts+=na; if(na>1) multi++
      ac=""; an=""; m=split($8,kv,";")
      for(i=1;i<=m;i++){ if(kv[i]~/^AC=/) ac=substr(kv[i],4); else if(kv[i]~/^AN=/) an=substr(kv[i],4) }
      k=split(ac,c,","); for(i=1;i<=k;i++) sac+=c[i]
      if(an!=5008) badan++
      if(na==1){
        r=toupper($4); x=toupper($5)
        if(x ~ /^</ || x ~ /[\[\]]/) oth++
        else if(length(r)==length(x)){
          d=0; for(i=1;i<=length(r);i++) if(substr(r,i,1)!=substr(x,i,1)) d++
          if(d==1) snp++; else mnp++
        } else indel++
      }
    }
    END{ printf "%.0f %.0f %.0f %.0f %.0f %.0f %.0f %.0f %.0f\n", n, alts, multi, sac, badan, snp, mnp, indel, oth }'
}
sn(){ awk -F'\t' -v k="$2" '$1=="SN" && $3==k":"{print $4}' "$1"; }

for C in $CHRS; do
  IN=$(vcf "$C")
  echo "================ chr$C ================"

  tic; read n_in alts_in multi_in sac_in badan_in _ _ _ _ < <(census "$IN"); toc "census_in_chr$C"
  rec "chr${C}_records_in" "$n_in"; rec "chr${C}_alt_alleles_in" "$alts_in"
  rec "chr${C}_multiallelic_in" "$multi_in"; rec "chr${C}_sum_AC_in" "$sac_in"
  chk "chr$C every input record has AN=5008 (2504 diploid samples)" "$badan_in" 0

  tic; bcftools stats "$IN" > stats.in.chr$C.txt 2> stats.in.chr$C.log; toc "stats_in_chr$C"
  chk "chr$C stats records == text census (input)" "$(sn stats.in.chr$C.txt 'number of records')" "$n_in"
  chk "chr$C stats multiallelic sites == text census (input)" "$(sn stats.in.chr$C.txt 'number of multiallelic sites')" "$multi_in"

  # ---- normalise: split every multiallelic, left-align against the reference ----
  tic
  bcftools norm -m -any -f "$REF" --threads "$T" -Oz -o norm.chr$C.vcf.gz --write-index "$IN" 2> norm.chr$C.log
  rc=$?; toc "norm_chr$C"
  cat norm.chr$C.log
  chk "chr$C bcftools norm exit" "$rc" 0
  L=$(grep -o 'skipped:[[:space:]]*[0-9/]*' norm.chr$C.log | awk '{print $2}')
  rec "chr${C}_norm_lines_total_split_joined_realigned_removed_skipped" "$L"
  # 1.24 reports total/split/joined/realigned/mismatch_removed/dup_removed/skipped
  IFS=/ read -r l_tot l_split l_join l_real l_mis l_dup l_skip <<< "$L"
  chk "chr$C norm read every input record" "$l_tot" "$n_in"
  chk "chr$C norm split exactly the multiallelic records" "$l_split" "$multi_in"
  chk "chr$C norm removed (REF mismatch, duplicate) and skipped nothing" "$l_mis/$l_dup/$l_skip" "0/0/0"
  rec "chr${C}_norm_realigned" "$l_real"

  tic; read n_out alts_out multi_out sac_out badan_out snp mnp indel oth < <(census norm.chr$C.vcf.gz); toc "census_norm_chr$C"
  rec "chr${C}_records_norm" "$n_out"
  rec "chr${C}_norm_snp_mnp_indel_other" "$snp/$mnp/$indel/$oth"
  chk "chr$C records out == ALT alleles in (one record per allele)" "$n_out" "$alts_in"
  chk "chr$C every output record is biallelic" "$alts_out/$multi_out" "$n_out/0"
  chk "chr$C sum of AC conserved through the split" "$sac_out" "$sac_in"
  chk "chr$C AN still 5008 on every split record" "$badan_out" 0
  chk "chr$C index count == records" "$(bcftools index -n norm.chr$C.vcf.gz)" "$n_out"

  # ---- recompute AC/AN from the genotypes; must equal the consortium's published tags ----
  tic
  bcftools query -f '%INFO/AC\t%INFO/AN\t%INFO/EAS_AF\t%INFO/AMR_AF\t%INFO/AFR_AF\t%INFO/EUR_AF\t%INFO/SAS_AF\n' norm.chr$C.vcf.gz > pub.chr$C.tsv
  bcftools +fill-tags norm.chr$C.vcf.gz -Ou -- -S pops.txt -t AC,AN,AF 2> filltags.chr$C.log \
    | bcftools query -f '%INFO/AC\t%INFO/AN\t%INFO/AF_EAS\t%INFO/AF_AMR\t%INFO/AF_AFR\t%INFO/AF_EUR\t%INFO/AF_SAS\n' > fill.chr$C.tsv
  ps="${PIPESTATUS[*]}"; toc "filltags_chr$C"
  chk "chr$C fill-tags | query exit" "$ps" "0 0"
  chk "chr$C recomputed rows / published rows == records" \
      "$(wc -l < fill.chr$C.tsv | tr -d ' ')/$(wc -l < pub.chr$C.tsv | tr -d ' ')" "$n_out/$n_out"
  read nrow acbad anbad nonnum afmax < <(paste pub.chr$C.tsv fill.chr$C.tsv | awk -F'\t' '
    { n++; if($1!=$8) acb++; if($2!=$9) anb++
      for(i=3;i<=7;i++){ if($i!=$i+0 || $(i+7)!=$(i+7)+0) { nan++; continue }
        d=$i-$(i+7); if(d<0)d=-d; if(d>m)m=d } }
    END{ printf "%d %d %d %d %.6g\n", n, acb+0, anb+0, nan+0, m+0 }')
  rec "chr${C}_superpop_AF_max_abs_diff" "$afmax"
  chk "chr$C AC recomputed from GT == published AC, every split record" "$acbad" 0
  chk "chr$C AN recomputed from GT == published AN, every split record" "$anbad" 0
  chk "chr$C every super-population AF is a number on both sides" "$nonnum" 0
  chk "chr$C super-population AF == published to its 4-decimal print precision (<=5e-5)" \
      "$(awk -v m="$afmax" 'BEGIN{print (m<=5e-5)?"yes":"no"}')" yes
  rm -f pub.chr$C.tsv fill.chr$C.tsv

  # ---- stats on the normalised file, against the text census ----
  tic; bcftools stats norm.chr$C.vcf.gz > stats.norm.chr$C.txt 2> stats.norm.chr$C.log; toc "stats_norm_chr$C"
  chk "chr$C stats records == census" "$(sn stats.norm.chr$C.txt 'number of records')" "$n_out"
  chk "chr$C stats SNPs == census" "$(sn stats.norm.chr$C.txt 'number of SNPs')" "$snp"
  chk "chr$C stats MNPs == census" "$(sn stats.norm.chr$C.txt 'number of MNPs')" "$mnp"
  # A length-changing non-symbolic allele is an indel to the census; bcftools files a few of them
  # (complex REF/ALT pairs) under "others" instead. So the classified identity is SNP and MNP
  # exactly, and indel+other as a sum; the records that move between the two are listed.
  s_ind=$(sn stats.norm.chr$C.txt 'number of indels'); s_oth=$(sn stats.norm.chr$C.txt 'number of others')
  rec "chr${C}_stats_indels_others" "$s_ind/$s_oth"
  chk "chr$C stats indels+others == census indels+others" "$(( s_ind + s_oth ))" "$(( indel + oth ))"
  bcftools query -i 'TYPE="other"' -f '%CHROM\t%POS\t%REF\t%ALT\n' norm.chr$C.vcf.gz \
    | awk -F'\t' '$4!~/^</' > other_nonsymbolic.chr$C.tsv
  rec "chr${C}_other_nonsymbolic" "$(wc -l < other_nonsymbolic.chr$C.tsv | tr -d ' ')"
  chk "chr$C the indel/other boundary is exactly the non-symbolic 'other' records" \
      "$(wc -l < other_nonsymbolic.chr$C.tsv | tr -d ' ')" "$(( indel - s_ind ))"
  head -5 other_nonsymbolic.chr$C.tsv
  chk "chr$C stats multiallelic sites after split" "$(sn stats.norm.chr$C.txt 'number of multiallelic sites')" 0

  # ---- filter: common SNPs (global AF >= 1%), the usual PCA / GWAS input ----
  tic
  bcftools view -v snps -i 'INFO/AF>=0.01' --threads "$T" -Oz -o common.chr$C.vcf.gz --write-index norm.chr$C.vcf.gz
  toc "filter_chr$C"
  ncom=$(bcftools index -n common.chr$C.vcf.gz)
  ncom_awk=$(bcftools query -f '%REF\t%ALT\t%INFO/AF\n' norm.chr$C.vcf.gz | awk -F'\t' '
      length($1)==1 && length($2)==1 && $2!~/^</ && $3+0>=0.01 {n++} END{print n+0}')
  rec "chr${C}_common_snps" "$ncom"
  chk "chr$C filter count == independent count of SNP && AF>=0.01" "$ncom" "$ncom_awk"

  # ---- annotate: strip ID + AF tags, restore them from a sites-only file; must be lossless ----
  tic
  bcftools view -G --threads "$T" -Oz -o sites.chr$C.vcf.gz --write-index norm.chr$C.vcf.gz
  Q='%CHROM\t%POS\t%ID\t%REF\t%ALT\t%INFO/AF\t%INFO/EUR_AF\t%INFO/AFR_AF\n'
  h0=$(bcftools query -f "$Q" norm.chr$C.vcf.gz | md5sum | cut -c1-32)
  # -a reads two files in lockstep (synced reader), so both must be indexed files, not a pipe
  bcftools annotate -x ID,INFO/AF,INFO/EUR_AF,INFO/AFR_AF --threads "$T" -Oz -o stripped.chr$C.vcf.gz \
    --write-index norm.chr$C.vcf.gz
  h1=$(bcftools annotate -a sites.chr$C.vcf.gz -c ID,INFO/AF,INFO/EUR_AF,INFO/AFR_AF -Ou stripped.chr$C.vcf.gz \
       | bcftools query -f "$Q" | md5sum | cut -c1-32)
  toc "annotate_chr$C"
  nid0=$(bcftools query -f '%ID\n' norm.chr$C.vcf.gz | grep -vc '^\.$')
  nidx=$(bcftools query -f '%ID\n' stripped.chr$C.vcf.gz | grep -vc '^\.$')
  rec "chr${C}_records_with_id" "$nid0"
  chk "chr$C (control) the strip removed every ID, and the original had them" "$nidx/$([ "$nid0" -gt 0 ] && echo some)" "0/some"
  chk "chr$C (control) the strip removed the AF header lines" \
      "$(bcftools view -h stripped.chr$C.vcf.gz | grep -cE '^##INFO=<ID=(AF|EUR_AF|AFR_AF),')" 0
  chk "chr$C annotate strip+restore from sites file is lossless" "$h1" "$h0"
  rm -f sites.chr$C.vcf.gz sites.chr$C.vcf.gz.csi stripped.chr$C.vcf.gz stripped.chr$C.vcf.gz.csi
done

# ---- concat across chromosomes, then split back: must be lossless ----
echo "================ concat ================"
NORMS=""; SUM=0
for C in $CHRS; do NORMS="$NORMS norm.chr$C.vcf.gz"; SUM=$(( SUM + $(bcftools index -n norm.chr$C.vcf.gz) )); done
tic; bcftools concat --threads "$T" -Oz -o all.norm.vcf.gz --write-index $NORMS; rc=$?; toc concat
chk "concat exit" "$rc" 0
NALL=$(bcftools index -n all.norm.vcf.gz)
rec all_records "$NALL"
chk "concat records == sum of per-chromosome records" "$NALL" "$SUM"
tic
for C in $CHRS; do
  a=$(bcftools view -H -r "$C" all.norm.vcf.gz | md5sum | cut -c1-32)
  b=$(bcftools view -H norm.chr$C.vcf.gz | md5sum | cut -c1-32)
  chk "chr$C concat -> region split round trip is byte-identical (records incl. all genotypes)" "$a" "$b"
done
toc concat_roundtrip
NC=""; for C in $CHRS; do NC="$NC common.chr$C.vcf.gz"; done
bcftools concat -Oz -o all.common.vcf.gz --write-index $NC
rec all_common_snps "$(bcftools index -n all.common.vcf.gz)"

# ---- merge across samples: split the cohort in two by sample, merge back ----
echo "================ merge (chr$MERGE_CHR) ================"
bcftools query -l norm.chr$MERGE_CHR.vcf.gz > samples.txt
NS=$(wc -l < samples.txt); rec samples "$NS"
chk "sample count" "$NS" 2504
head -n 1252 samples.txt > s1.txt; tail -n +1253 samples.txt > s2.txt
tic
bcftools view -S s1.txt --no-update --threads "$T" -Oz -o half1.vcf.gz --write-index norm.chr$MERGE_CHR.vcf.gz
bcftools view -S s2.txt --no-update --threads "$T" -Oz -o half2.vcf.gz --write-index norm.chr$MERGE_CHR.vcf.gz
toc "sample_split_chr$MERGE_CHR"
tic; bcftools merge -m none --threads "$T" -Oz -o merged.vcf.gz half1.vcf.gz half2.vcf.gz 2> merge.log; rc=$?; toc "merge_chr$MERGE_CHR"
chk "merge exit" "$rc" 0
G='%CHROM\t%POS\t%REF\t%ALT[\t%GT]\n'
tic
g0=$(bcftools query -f "$G" norm.chr$MERGE_CHR.vcf.gz | md5sum | cut -c1-32)
g1=$(bcftools query -f "$G" merged.vcf.gz | md5sum | cut -c1-32)
nm=$(bcftools view -H merged.vcf.gz | wc -l)
toc "merge_check_chr$MERGE_CHR"
chk "merged samples == original order" "$(bcftools query -l merged.vcf.gz | md5sum | cut -c1-32)" "$(md5sum < samples.txt | cut -c1-32)"
chk "merge records == original records" "$nm" "$(bcftools index -n norm.chr$MERGE_CHR.vcf.gz)"
chk "sample split + merge reproduces every genotype of every record" "$g1" "$g0"
rm -f half1.vcf.gz half1.vcf.gz.csi half2.vcf.gz half2.vcf.gz.csi merged.vcf.gz

kill "$SAMPLER" 2>/dev/null
rec peak_rss_kib "$(awk 'BEGIN{m=0} $1>m{m=$1} END{print m}' rss.samples)"
rec checks_failed "$FAILS"
echo "== checks =="; cat checks.tsv
if [ "$FAILS" = 0 ]; then echo "BCFTOOLS REAL OK"; else echo "BCFTOOLS REAL FAILED: $FAILS checks"; exit 1; fi
