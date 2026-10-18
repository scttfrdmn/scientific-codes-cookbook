#!/usr/bin/env bash
# PLINK 2's real workflow over 1000 Genomes Phase 3 (all 2504 samples, chromosomes 1-22): VCF
# import, counts, QC, LD pruning, PCA, a GWAS on a phenotype with planted effects, and the
# identities that must hold. plink19.sh then runs PLINK 1.9 on the same VCF bytes and compares.
#
#   p2.sh <threads> [chromosomes...]      (default 1..22)
#
# Inputs in /tmp: ALL.chr<N>.*.vcf.gz (inputs/plink-real/, shared with recipes/plink's real run),
# vcf.sha256, panel. The image has no python3, so everything here is awk.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
cd /tmp
T="${1:?threads}"; shift
CHR="${*:-$(seq 1 22)}"
: > score.tsv
rec()  { printf '%s\t%s\n' "$1" "$2" | tee -a score.tsv; }
fail() { echo "FAIL: $*" | tee -a score.tsv; tar cf logs.tar log.*.txt 2>/dev/null; exit 1; }
now()  { echo "${EPOCHREALTIME:-$(date +%s)}"; }   # BusyBox date has no %N
lap()  { awk -v a="$1" -v b="$(now)" 'BEGIN{printf "%.1f", b-a}'; }
run()  { # run <label> args... : one timed plink2 call, log kept, task failed on non-zero
  local l="$1"; shift; local t; t=$(now)
  plink2 "$@" --threads "$T" > "log.$l.txt" 2>&1 || { tail -20 "log.$l.txt"; fail "plink2 $l"; }
  rec "wall_$l" "$(lap "$t")"
}
hdr='FNR==1{for(i=1;i<=NF;i++){h=$i;sub(/^#/,"",h);c[h]=i};next}'
T0=$(now)

sha256sum -c vcf.sha256 > /dev/null || fail "a VCF or the panel does not match its sha256"
rec inputs_sha256 "$(awk 'END{print NR}' vcf.sha256) files OK"
rec plink2_version "$(plink2 --version 2>&1 | awk 'NR==1')"
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=unknown ;; esac
EM=$(od -An -tu1 -j18 -N2 "$(command -v plink2)" | awk '{print $1 + $2*256}')
test "$EM" = "$WANT" || fail "plink2 e_machine $EM on $(uname -m)"
rec arch "$(uname -m) e_machine=$EM nproc=$(nproc)"

# ------------------------------------------------------------------- 1. import and merge
# Biallelic ACGT SNPs only -- the same set recipes/plink's PLINK 1.9 import keeps
# (--snps-only just-acgt --biallelic-only strict). IDs become CHR:POS:REF:ALT: 1000G's own IDs
# are not unique ('.' and repeated rsIDs), and --extract needs them to be.
# Right after each import, the consortium's own counts: every record carries INFO AC (ALT
# alleles) and AN (called alleles), computed by 1000G from the same genotypes. PLINK 2's
# --freq must equal both, exactly, on every variant. Row-aligned streams, so no table in memory.
t=$(now); : > merge.txt; : > dup.txt; XR=0; NK=0
for c in $CHR; do
  run "import_chr$c" --vcf ALL.chr$c.phase3_*.vcf.gz --snps-only just-acgt --max-alleles 2 \
      --set-all-var-ids '@:#:$r:$a' --new-id-max-allele-len 1000 missing --make-pgen --out "c$c"
  plink2 --pfile "c$c" --freq counts --threads "$T" --out "c$c" > "log.freq_chr$c.txt" 2>&1 || fail "freq chr$c"
  paste <(awk '!/^#/{ac="";an=""; k=split($NF,f,";"); for(i=1;i<=k;i++){ if(f[i]~/^AC=/)ac=substr(f[i],4); if(f[i]~/^AN=/)an=substr(f[i],4) }; print $3, ac, an}' "c$c.pvar") \
        <(awk 'FNR>1{print $2, $(NF-1), $NF}' "c$c.acount") |
    awk '{n++; if($1!=$4) bi++; if($2!=$5) b1++; if($3!=$6) b2++} END{printf "%d %d %d %d\n", n, bi+0, b1+0, b2+0}' > x.txt
  read -r N BI XAC XAN < x.txt
  test "$BI" = 0 -a "$XAC" = 0 -a "$XAN" = 0 || fail "chr$c: $XAC ALT counts / $XAN totals differ from INFO AC/AN ($BI misaligned)"
  XR=$((XR + N))
  # INFO has done its job; it is ~11 GB of .pvar text genome-wide, so drop it chromosome by
  # chromosome. Records repeating a CHR:POS:REF:ALT go too, every copy: --pmerge-list silently
  # collapsed 4 of them (measured), and PLINK 1.9 is handed the same list to exclude.
  plink2 --pfile "c$c" --rm-dup exclude-all list --make-pgen pvar-cols=maybecm --threads "$T" --out "d$c" > "log.slim_chr$c.txt" 2>&1 || fail "slim chr$c"
  test -s "d$c.rmdup.list" && cat "d$c.rmdup.list" >> dup.txt
  NK=$((NK + $(awk '!/^#/{n++}END{print n+0}' "d$c.pvar")))
  rm -f "c$c.pgen" "c$c.pvar" "c$c.psam" "c$c.acount"
  echo "d$c" >> merge.txt
done
run merge --pmerge-list merge.txt pfile --make-pgen pvar-cols=maybecm --out kg
while read -r p; do rm -f "$p.pgen" "$p.pvar" "$p.psam"; done < merge.txt
rec wall_import_total "$(lap "$t")"
NV=$(awk '!/^#/{n++}END{print n+0}' kg.pvar); NS=$(awk '!/^#/{n++}END{print n+0}' kg.psam)
rec variants "$NV"; rec samples "$NS"
test "$NS" = 2504 || fail "expected the 2504 Phase 3 samples, got $NS"
rec identity_vs_1000G_INFO_AC_AN "$XR of $XR exact"
rec duplicate_records_removed "$((XR - NK)) records, $(awk 'END{print NR}' dup.txt) IDs"
test "$NK" = "$NV" || fail "$NK variants into --pmerge-list, $NV out"
DUP=$(awk '!/^#/{print $3}' kg.pvar | sort | uniq -d | awk 'END{print NR}')
test "$DUP" = 0 || fail "$DUP duplicate variant IDs"

# ------------------------------------------------------------------- 2. counts
run counts --pfile kg --freq counts --hardy --missing --out all
# (b) Genotype classes partition the samples, and --hardy's genotype counts imply --freq's
#     allele count: two code paths, one integer.
# .hardy columns: #CHROM ID A1 AX HOM_A1_CT HET_A1_CT TWO_AX_CT ...; .acount ends ALT_CTS OBS_CT.
paste <(awk 'FNR>1{print $2, $(NF-1)}' all.acount) <(awk 'FNR>1{print $2, $5, $6, $7}' all.hardy) |
  awk '{ if($1!=$3) bi++; a=$4; h=$5; x=$6; if(a+h+x!='"$NS"') bs++; if(2*a+h!=$2 && 2*x+h!=$2) bx++ }
       END{ printf "%d %d\n", bs+bi+0, bx+0 }' > a.txt
read -r BS BX < a.txt
test "$BS" = 0 || fail "$BS variants whose genotype classes do not sum to $NS"
test "$BX" = 0 || fail "$BX variants where --freq and --hardy disagree on the allele count"
rec identity_hardy_partitions_and_implies_freq "$NV of $NV"
rec missing_genotypes "$(awk "$hdr"'{s+=$(c["MISSING_CT"])}END{print s+0}' all.vmiss)"

# ------------------------------------------------------------------- 3. QC
# MAF >= 1%, variant and sample call rate >= 98%. No HWE filter: 26 pooled populations violate
# HWE by construction (Wahlund), so a pooled HWE filter would remove real ancestry signal.
run qc --pfile kg --maf 0.01 --geno 0.02 --mind 0.02 --write-snplist --write-samples --out qc
NQ=$(awk 'END{print NR}' qc.snplist); NQS=$(awk '!/^#/{n++}END{print n+0}' qc.id)
NR1=$(awk "$hdr"'{a=$(c["ALT_CTS"]); o=$(c["OBS_CT"]); m=(a<o-a?a:o-a); if(m>=0.01*o) n++} END{print n+0}' all.acount)
rec qc_variants "$NQ"; rec qc_samples "$NQS"
test "$NQ" = "$NR1" || fail "--maf kept $NQ variants; recounting .acount's integers gives $NR1"
rec identity_maf_recount "$NQ == $NR1"

# ------------------------------------------------------------------- 4. prune + PCA
printf '6 25000000 34000000 MHC\n8 7000000 13000000 inv8p23\n' > lrld.txt
run prune --pfile kg --extract qc.snplist --maf 0.05 --exclude bed1 lrld.txt \
          --indep-pairwise 1000kb 0.1 --out prune
rec pruned_in "$(awk 'END{print NR}' prune.prune.in)"
# approx is randomized: unseeded, two runs on one box gave PC1 opposite signs (measured).
run pca_approx --pfile kg --extract prune.prune.in --pca 10 approx --seed 20261017 --out pca
run pca_exact  --pfile kg --extract prune.prune.in --pca 10 --out pcax
# Eigenvectors of the centred GRM are orthonormal and orthogonal to the all-ones vector (every
# PC sums to zero). Algebra, not observation; the bounds are the 6 significant figures PLINK
# prints each loading with, summed over 2504 samples.
for f in pca pcax; do
  awk 'NR==1{next}{ n++; for(k=1;k<=10;k++){v[n,k]=$(NF-10+k); s[k]+=v[n,k]} }
       END{ for(k=1;k<=10;k++){ a=s[k]<0?-s[k]:s[k]; if(a>ms)ms=a
              for(l=k;l<=10;l++){ d=0; for(i=1;i<=n;i++) d+=v[i,k]*v[i,l]; if(k==l)d-=1; if(d<0)d=-d; if(d>md)md=d } }
            printf "%.3e %.3e\n", md, ms }' "$f.eigenvec" > o.txt
  read -r MD MSUM < o.txt
  rec "identity_${f}_orthonormal_maxdev__sum_max" "$MD  $MSUM"
  awk -v a="$MD" -v b="$MSUM" 'BEGIN{exit !(a+0 < 1e-4 && b+0 < 1e-3)}' || fail "$f eigenvectors not orthonormal / centred"
done
paste pca.eigenval pcax.eigenval | awk '{d=($1-$2)/$2; d=d<0?-d:d; if(d>m)m=d; printf "%s/%s ", $1, $2} END{printf "  max_rel=%.2e\n", m}' > ev.txt
rec eigenval_approx_vs_exact "$(cat ev.txt)"
awk 'NR==FNR{if(FNR>1)sp[$1]=$3; next} FNR==1{next}
     {p=sp[$(NF-10)]; n[p]++; a[p]+=$(NF-9); b[p]+=$(NF-8)}
     END{for(p in n) printf "%s:%d:%+.4f:%+.4f ", p, n[p], a[p]/n[p], b[p]/n[p]; printf "\n"}' panel pca.eigenvec | tr ' ' '\n' | sort | tr '\n' ' ' > sp.txt
rec superpop_n_meanPC1_meanPC2 "$(cat sp.txt)"

# ------------------------------------------------------------------- 5. planted GWAS
# One causal SNP per odd autosome: the first QC'd, pruned-in SNP with MAF >= 0.25. Even
# autosomes carry none and are the null set for genomic control. An AFR shift of +1.0 plants
# stratification that only the PCs remove. Noise: a Park-Miller LCG summed 12 times
# (Irwin-Hall, var 1) -- integer arithmetic exact in doubles, so the phenotype bytes cannot
# depend on the awk or the chip.
awk 'FILENAME=="prune.prune.in"{ok[$1]=1; next}
     FILENAME=="all.acount"{ if(FNR>1 && ($2 in ok)){ a=$(NF-1); o=$NF; maf[$2]=(a<o-a?a:o-a)/o }; next }
     /^#/{next}
     { if(($1%2)==1 && !($1 in got) && ($3 in maf) && maf[$3]>=0.25){ got[$1]=1; print $3 } }' \
     prune.prune.in all.acount kg.pvar > causal.txt
NC=$(awk 'END{print NR}' causal.txt)
printf '0.30\n-0.25\n0.20\n-0.15\n0.10\n0.30\n-0.25\n0.20\n-0.15\n0.10\n0.25\n' | head -n "$NC" | paste causal.txt - > beta.txt
run export --pfile kg --extract causal.txt --export A --out causal
# .raw columns are read by name: IID, then one <ID>_<counted allele> column per causal SNP.
# --export A may count REF (it did here), so each dosage not counting ALT (the ID ends :ALT) is flipped: every planted
# beta is per ALT allele.
awk 'FILENAME=="panel"{ if(FNR>1) sp[$1]=$3; next }
     FILENAME=="beta.txt"{ b[$1]=$2; next }
     FNR==1{ for(i=1;i<=NF;i++){ h=$i; sub(/^#/,"",h); if(h=="IID") ic=i
                                 else if(h!~/^(FID|PAT|MAT|SEX|PHENOTYPE)$/){ id=h; sub(/_[^_]*$/,"",id); gi[++ng]=i; col[i]=id
                                   al=h; sub(/.*_/,"",al); alt=id; sub(/.*:/,"",alt); fl[i]=(al!=alt) } }
             x=20261017; next }
     { y=0; for(j=1;j<=ng;j++){ i=gi[j]; y += b[col[i]] * (fl[i] ? 2-$i : $i) }
       if(sp[$ic]=="AFR") y += 1.0
       e=0; for(k=0;k<12;k++){ x=(16807*x)%2147483647; e+=x/2147483647 }
       printf "%s %.10f\n", $ic, y + e - 6 }' panel beta.txt causal.raw > y.txt
test "$(awk 'END{print NR}' y.txt)" = 2504 || fail "phenotype has the wrong row count"
# The same values in both PLINKs' formats: #IID for PLINK 2's FID-less .psam, FID=IID for 1.9's --double-id.
{ echo "#IID Y"; cat y.txt; } > pheno2.txt
{ echo "FID IID Y"; awk '{print $1, $1, $2}' y.txt; } > pheno.txt
rec pheno_sha256 "$(sha256sum y.txt | cut -c1-16)"
awk 'NR==1{printf "#IID"; for(k=1;k<=10;k++) printf " PC%d", k; printf "\n"; next}
     {printf "%s", $(NF-10); for(k=NF-9;k<=NF;k++) printf " %s", $k; printf "\n"}' pca.eigenvec > covar2.txt
awk 'NR==1{sub(/^#IID/,"FID IID")} NR>1{$0=$1" "$0} {print}' covar2.txt > covar.txt
run glm_pc   --pfile kg --extract qc.snplist --pheno pheno2.txt --pheno-name Y --covar covar2.txt \
             --covar-variance-standardize --glm hide-covar --out gwas_pc
run glm_nopc --pfile kg --extract qc.snplist --pheno pheno2.txt --pheno-name Y --glm allow-no-covars --out gwas_nopc
G=gwas_pc.Y.glm.linear
test "$(awk 'END{print NR-1}' $G)" = "$NQ" || fail "--glm did not test every QC'd variant"
rec glm_rows "$NQ"
# Recovery: each planted beta within 4 SE (P(any of 11 outside) = 7e-4 for a correct estimator),
# and the standardized errors jointly below chi-square(11)'s 0.999 quantile, 31.26. Deterministic
# run, so neither can flake: they hold or the estimator is wrong.
# --glm's A1 is the MINOR allele, not ALT (measured: REF on 5 of the 11), so each planted beta
# is put on A1's scale first: per-REF = -(per-ALT). Columns: chr id A1 planted_on_A1 beta se z p.
awk 'NR==FNR{b[$1]=$2; next} '"$hdr"' ($(c["ID"]) in b){ id=$(c["ID"]); alt=id; sub(/.*:/,"",alt)
       t=($(c["A1"])==alt) ? b[id] : -b[id]; z=($(c["BETA"])-t)/$(c["SE"])
       printf "%s\t%s\t%s\t%s\t%s\t%s\t%.3f\t%s\n", $1, id, $(c["A1"]), t, $(c["BETA"]), $(c["SE"]), z, $(c["P"]) }' beta.txt $G > recovery.tsv
cat recovery.tsv
awk '{n++; z2+=$7*$7; if($7>4||$7<-4) bad++} END{printf "%d %d %.3f\n", n, bad+0, z2}' recovery.tsv > r.txt
read -r RN RBAD RZ2 < r.txt
rec planted_recovered "$RN of $NC, $RBAD outside 4 SE, sum z^2 = $RZ2"
test "$RN" = "$NC" -a "$RBAD" = 0 || fail "planted effects not recovered"
awk -v z="$RZ2" 'BEGIN{exit !(z+0 < 31.26)}' || fail "sum z^2 = $RZ2 above chi2(11) 0.999 quantile"
for g in pc nopc; do
  awk "$hdr"'($1%2)==0{t=$(c["T_STAT"]); print t*t}' gwas_$g.Y.glm.linear | sort -g > t2.txt
  rec "lambda_gc_even_autosomes_$g" "$(awk '{v[NR]=$1} END{m=(NR%2)?v[(NR+1)/2]:(v[NR/2]+v[NR/2+1])/2; printf "%.4f (n=%d)", m/0.454936, NR}' t2.txt)"
  rm -f t2.txt
done
rec genome_wide_significant_pc "$(awk "$hdr"'$(c["P"])<5e-8{n++}END{print n+0}' $G)"

# ------------------------------------------------------------------- 6. hand-off to PLINK 1.9
# Slim per-variant tables, in .pvar order: ID, minor-allele count, observed alleles, HWE P.
paste <(awk "$hdr"'{a=$(c["ALT_CTS"]); o=$(c["OBS_CT"]); print $(c["ID"]) "\t" (a<o-a?a:o-a) "\t" o}' all.acount) \
      <(awk "$hdr"'{print $(c["P"])}' all.hardy) | gzip -1 > p2.counts.tsv.gz
awk 'NR==FNR{k[$1]=1;next} FNR==1{print;next} ($3 in k)' causal.txt $G > glm.causal.tsv
tar cf logs.tar log.*.txt *.log dup.txt
rec wall_task "$(lap "$T0")"
PEAK=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes 2>/dev/null || echo 0)
rec peak_mem_gib "$(awk -v p="$PEAK" 'BEGIN{printf "%.2f", p/1073741824}')"
rec tmp_used_gib "$(df -k /tmp | awk 'NR==2{printf "%.1f", $3/1048576}')"
echo "PLINK2 REAL OK"
