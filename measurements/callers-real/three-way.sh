set -eo pipefail
export PATH=/opt/conda/bin:$PATH
cd /tmp
zcat freebayes.vcf.gz | bcftools view -Oz -o fb.vcf.gz
for v in gatk bcftools fb; do bcftools index -f -t $v.vcf.gz; done

# Truth side, once. GIAB scores only inside the BED it ships.
bcftools view -R truth.chr20.bed -i 'GT="alt"' -Ou truth.chr20.vcf.gz | bcftools norm -f chr20.fa -m -any -Oz -o t.vcf.gz
bcftools index -f t.vcf.gz

# score TOOL TYPE QUALMIN -- QUALMIN 0 means unfiltered.
score(){
  local tool=$1 ty=$2 qm=$3 tag="${1}_${2}_q${3}"
  bcftools view -R truth.chr20.bed -Ou $tool.vcf.gz | bcftools norm -f chr20.fa -m -any -Oz -o q_$tag.vcf.gz
  bcftools index -f q_$tag.vcf.gz
  if [ "$qm" = "0" ]; then
    bcftools view -v "$ty" -Oz -o qq_$tag.vcf.gz q_$tag.vcf.gz
  else
    bcftools view -v "$ty" -i "QUAL>=$qm" -Oz -o qq_$tag.vcf.gz q_$tag.vcf.gz
  fi
  bcftools view -v "$ty" -Oz -o tt_$tag.vcf.gz t.vcf.gz
  bcftools index -f qq_$tag.vcf.gz
  bcftools index -f tt_$tag.vcf.gz
  rm -rf is_$tag
  bcftools isec -p is_$tag qq_$tag.vcf.gz tt_$tag.vcf.gz
  local FP FN TP
  FP=$(grep -vc '^#' is_$tag/0000.vcf || true)
  FN=$(grep -vc '^#' is_$tag/0001.vcf || true)
  TP=$(grep -vc '^#' is_$tag/0002.vcf || true)
  awk -v tag="$tag" -v tp="$TP" -v fp="$FP" -v fn="$FN" 'BEGIN{
    p=(tp+fp)>0?tp/(tp+fp):0; r=(tp+fn)>0?tp/(tp+fn):0; f=(p+r)>0?2*p*r/(p+r):0;
    printf "%s %d %d %d %.5f %.5f %.5f\n", tag,tp,fp,fn,p,r,f }'
}

: > rows.txt
for tool in gatk bcftools fb; do
  for ty in snps indels; do
    for qm in 0 30; do
      score $tool $ty $qm >> rows.txt
    done
  done
done

{
  echo "== three callers vs GIAB HG001 v4.2.1, chr20, high-confidence regions =="
  echo ""
  echo "QUAL is NOT calibrated across callers, so a shared numeric threshold cannot rank them."
  echo "q0 = the tool's full output (like-with-like by construction); q30 = QUAL>=30 on each."
  echo ""
  printf "%-22s %8s %7s %7s %10s %10s %10s\n" tag TP FP FN precision recall F1
  while read -r tag tp fp fn p r f; do
    printf "%-22s %8s %7s %7s %10s %10s %10s\n" "$tag" "$tp" "$fp" "$fn" "$p" "$r" "$f"
  done < rows.txt
} > three-way.txt
cat three-way.txt

# Two assertions, both claims every working caller must satisfy, neither shaved to the observed
# values. (1) UNFILTERED SNV recall >= 0.95: the caller detects the truth variants at all,
# independent of how it scales QUAL. (2) QUAL>=30 SNV precision >= 0.98: the caller CAN be
# filtered to high precision. Both fail loudly on a broken reference, sample or depth.
#
# What is deliberately NOT asserted is a shared recall floor high enough to separate the three.
# freebayes reaches only 0.9595 unfiltered at default settings while gatk and bcftools reach
# 0.995 -- that gap is the finding, and an assertion tuned to admit it would be a fudge, while
# one tuned to exclude it would fail a correctly-working tool.
for tool in gatk bcftools fb; do
  R0=$(awk -v k="${tool}_snps_q0" '$1==k{print $6}' rows.txt)
  P30=$(awk -v k="${tool}_snps_q30" '$1==k{print $5}' rows.txt)
  echo "$tool: unfiltered SNV recall $R0, QUAL>=30 SNV precision $P30"
  awk -v v="$R0"  'BEGIN{exit !(v>=0.95)}'
  awk -v v="$P30" 'BEGIN{exit !(v>=0.98)}'
done
echo 'THREE-WAY CHECK PASSED'
