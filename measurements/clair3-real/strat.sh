#!/usr/bin/env bash
# Task 03 (bcftools image, Graviton4, once): stratify vcfeval's TP/FP/FN records by GIAB v3.3's
# GRCh38 stratifications, the way hap.py does, check the bookkeeping, and compare the three chips.
# A record belongs to a stratum when its POS lies in it (bcftools -T, pos overlap), which makes a
# stratum and its complement an exact partition of the counts.
set +e
set -uo pipefail
cd /tmp
. ./lib.sh
machine
rec bcftools_version "$(bcftools --version | head -1)"
CHIPS="arm64 x86_64 amd"
STRATA="all easy alldifficult lowmapsegdup notlowmapsegdup trhomopolymer segdups lowmap complexsv"

awk -F'\t' '$1=="chr20"' bench.bed > "$W/all.bed"
# (loop variables avoid `s`: lib.sh's chk sets it)
for st in $STRATA; do [ "$st" = all ] || gzip -dc "strat.$st.bed.gz" | awk -F'\t' '$1=="chr20"' > "$W/$st.bed"
  rec "stratum_${st}_chr20_bases" "$(awk '{n+=$3-$2} END{print n+0}' "$W/$st.bed")"; done

# SNP: REF and every ALT one base; anything else (indels, MNPs, complex) is INDEL, as hap.py splits it
cnt(){ # file [bed] -> "snp indel"
  if [ -n "${2:-}" ]; then bcftools view -H -T "$2" "$1"; else bcftools view -H "$1"; fi |
    awk -F'\t' '{if (length($4)==1 && $5 ~ /^[ACGT](,[ACGT])*$/) s++; else i++} END{print s+0, i+0}'; }

: > "$W/counts.tsv"
for c in $CHIPS; do for st in $STRATA; do
  bed="$W/$st.bed"; [ "$st" = all ] && bed=""
  for f in tp-baseline fn tp fp; do
    set -- $(cnt "$c/$f.vcf.gz" "$bed")
    printf '%s\t%s\t%s\tSNP\t%s\n%s\t%s\t%s\tINDEL\t%s\n' "$c" "$st" "$f" "$1" "$c" "$st" "$f" "$2" >> "$W/counts.tsv"
  done; done; done
get(){ awk -F'\t' -v c="$1" -v s="$2" -v f="$3" -v t="$4" '$1==c && $2==s && $3==f && $4==t {print $5}' "$W/counts.tsv"; }
sum(){ awk -F'\t' -v c="$1" -v s="$2" -v f="$3" '$1==c && $2==s && $3==f {n+=$5} END{print n+0}' "$W/counts.tsv"; }

# the hap.py-shaped table: one row per chip x stratum x type
{ printf 'chip\tstratum\ttype\ttruth_total\ttruth_tp\ttruth_fn\tquery_total\tquery_tp\tquery_fp\trecall\tprecision\tf1\n'
  for c in $CHIPS; do for st in $STRATA; do for t in SNP INDEL; do
    tb=$(get $c $st tp-baseline $t); fn=$(get $c $st fn $t); tc=$(get $c $st tp $t); fp=$(get $c $st fp $t)
    awk -v c=$c -v s=$st -v t=$t -v tb=$tb -v fn=$fn -v tc=$tc -v fp=$fp 'BEGIN{
      r=(tb+fn)?tb/(tb+fn):0; p=(tc+fp)?tc/(tc+fp):0; f=(r+p)?2*r*p/(r+p):0
      printf "%s\t%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%.6f\t%.6f\t%.6f\n", c,s,t,tb+fn,tb,fn,tc+fp,tc,fp,r,p,f}'
  done; done; done; } > "$O/stratified.tsv"
cat "$O/stratified.tsv"
F(){ awk -F'\t' -v c="$1" -v s="$2" -v t="$3" -v k="$4" 'NR==1{for(i=1;i<=NF;i++) h[$i]=i; next}
       $1==c && $2==s && $3==t {print $h[k]}' "$O/stratified.tsv"; }

for c in $CHIPS; do
  # 1. vcfeval's own summary == the records it wrote (its counts are records, no threshold)
  for p in "tp_baseline tp-baseline" "tp_call tp" "fp fp" "fn fn"; do set -- $p
    chk "$c vcfeval summary $1 == $2.vcf.gz records" "$(awk -F'\t' -v k="${c}_summary_$1" '$1==k{print $2}' eval.results.tsv)" "$(sum $c all $2)"
  done
  # 2. a stratum and its complement partition every count exactly (POS-based membership)
  for pr in "easy alldifficult" "notlowmapsegdup lowmapsegdup"; do set -- $pr
    for f in tp-baseline fn tp fp; do for t in SNP INDEL; do
      chk "$c $f $t: $1 + $2 == all" "$(( $(get $c $1 $f $t) + $(get $c $2 $f $t) ))" "$(get $c all $f $t)"
    done; done; done
  # 3. the query side: TP + FP == the chip's PASS calls overlapping the benchmark regions (vcfeval
  #    takes a record that overlaps a region, so this count is by record overlap, not POS)
  chk "$c query TP + FP == PASS calls overlapping the benchmark regions" "$(( $(sum $c all tp) + $(sum $c all fp) ))" \
      "$(bcftools view -H -f PASS -T "$W/all.bed" --targets-overlap record "$c.vcf.gz" | wc -l | tr -d ' ')"
  # 4. accuracy floors, broken-run guards (see README): SNPs everywhere, indels in GIAB's easy regions.
  #    Indel F1 over all regions is reported, not asserted -- every indel error is in a tandem repeat
  for p in "SNP all 0.995" "SNP easy 0.999" "INDEL easy 0.99"; do set -- $p
    f1=$(F $c $2 $1 f1)
    chk "$c $1 F1 >= $3 ($2)" "$(awk -v f="$f1" -v l=$3 'BEGIN{print (f>=l)?"yes":"no: " f}')" yes
  done
done
# 5. the truth side is a property of the truth set and regions, never of the caller: identical across chips
for st in $STRATA; do for t in SNP INDEL; do
  chk "truth total $st $t: x86_64 == arm64" "$(F x86_64 $st $t truth_total)" "$(F arm64 $st $t truth_total)"
  chk "truth total $st $t: amd == arm64" "$(F amd $st $t truth_total)" "$(F arm64 $st $t truth_total)"
done; done
# ... and equals the truth's own non-reference records inside the regions
chk "truth TP + FN == truth records with an ALT genotype overlapping the benchmark regions" "$(( $(sum arm64 all tp-baseline) + $(sum arm64 all fn) ))" \
    "$(bcftools view -H -i 'GT="alt"' -T "$W/all.bed" --targets-overlap record bench.chr20.vcf.gz | wc -l | tr -d ' ')"

# across chips: reported, not asserted -- a neural network's scores may be a path
sites(){ bcftools query -f '%CHROM\t%POS\t%REF\t%ALT\t%FILTER\t[%GT]\n' "$1" | sort; }
full(){ bcftools query -f '%CHROM\t%POS\t%REF\t%ALT\t%FILTER\t%QUAL\t[%GT\t%GQ]\n' "$1" | sort; }
sites arm64.vcf.gz > "$W/a.sites"; full arm64.vcf.gz > "$W/a.full"
for c in x86_64 amd; do
  sites $c.vcf.gz > "$W/$c.sites"; full $c.vcf.gz > "$W/$c.full"
  rec "${c}_vs_arm64_rows_differing_site_filter_gt" "$(comm -3 "$W/a.sites" "$W/$c.sites" | wc -l | tr -d ' ')"
  rec "${c}_vs_arm64_rows_differing_incl_qual_gq" "$(comm -3 "$W/a.full" "$W/$c.full" | wc -l | tr -d ' ')"
  comm -3 "$W/a.sites" "$W/$c.sites" | head -200 > "$O/diff.sites.$c.txt"
  comm -3 "$W/a.full" "$W/$c.full" | head -200 > "$O/diff.full.$c.txt"
  for f in tp-baseline fn tp fp; do
    rec "${c}_vs_arm64_${f}_records_differing" "$(comm -3 <(bcftools view -H arm64/$f.vcf.gz | cut -f1-5,10 | sort) \
       <(bcftools view -H $c/$f.vcf.gz | cut -f1-5,10 | sort) | wc -l | tr -d ' ')"
  done
done
finish STRAT
