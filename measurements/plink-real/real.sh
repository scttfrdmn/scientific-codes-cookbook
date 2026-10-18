#!/usr/bin/env bash
# PLINK 1.9 on the 1000 Genomes Phase 3 autosomes: import, QC, LD pruning, PCA and a
# PC-adjusted association scan, with every claim checked against 1000G's own INFO fields.
#
#   CAUSAL=<chr:pos:ref:alt> bash real.sh <dir with the staged files> <chromosomes...>
#
# Portable glue only: bioconda's x86 image is BusyBox (no join/numfmt/perl, BusyBox awk/sort),
# and spawn runs task commands under bash -e (spawn#707), so errexit is cleared explicitly.
set +e
set -uo pipefail
IN="$1"; shift
CHROMS="$*"
NCPU=$(nproc)                    # read before OMP_NUM_THREADS=1 below, which GNU nproc honours
NJOB="${NJOB:-$NCPU}"
W=/var/tmp/plink-real            # container disk: the beds do not belong in the /tmp tmpfs
OUT=/tmp/out                     # staged out
mkdir -p "$W" "$OUT"
cd "$W" || exit 1
RES="$OUT/results.tsv"; : > "$RES"
FAILS=0
rec()  { printf '%s\t%s\n' "$1" "$2" >> "$RES"; printf '%-34s %s\n' "$1" "$2"; }
must() { if [ "$1" = ok ]; then rec "assert:$2" ok; else rec "assert:$2" "FAIL ($3)"; FAILS=$((FAILS+1)); fi; }
ts()   { date +%s; }
vcf()  { echo "$IN/ALL.chr$1.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"; }
# One chromosome per core, so each PLINK is single-threaded -- and so is its BLAS. OpenBLAS
# otherwise starts a thread per core in EVERY process: 16 x 16 threads on 16 cores made the
# --linear scan ~50x slower (measured: 80 SNPs/s per process against ~4,000 with 2 processes).
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
P="plink --keep-allele-order --threads 1 --memory 1500 --allow-no-sex"
# bounded parallelism with no xargs -P (BusyBox's may lack it). This shell's only children are
# the pool's jobs -- the footprint sampler runs in the caller -- so `wait` sees only them.
pool() { local fn=$1; shift; local n=0; for c in "$@"; do "$fn" "$c" & n=$((n+1)); if [ $n -ge "$NJOB" ]; then wait -n; n=$((n-1)); fi; done; wait; }
sum()  { awk -v k="$1" '{s+=$k} END{print s+0}'; }

plink --version > plinkver.txt 2>&1
rec plink_version "$(awk 'NR==1{print $2}' plinkver.txt)"
rec host "$(uname -m) $NCPU cpus"
case "$(uname -m)" in aarch64) WANT_EM=183 ;; x86_64) WANT_EM=62 ;; *) WANT_EM=unknown ;; esac
EM=$(od -An -tu1 -j18 -N2 "$(command -v plink)" | awk '{print $1 + $2*256}')
[ "$EM" = "$WANT_EM" ] && must ok native_elf || must no native_elf "e_machine $EM want $WANT_EM"

# super-population clusters, from 1000G's own panel
awk 'NR>1{print $1, $1, $3}' "$IN/integrated_call_samples_v3.20130502.ALL.panel" > superpop.clst
awk '$3=="EUR"{print $1, $2}' superpop.clst > eur.keep

# ---------- 1. per chromosome: import, QC, and every identity against the INFO truth ----------
# Lockstep reads, not hash tables: chr2 is 6.5M SNPs and BusyBox awk is the x86 awk. Each
# chromosome writes one line of tallies to id.<c> and deletes its big text files.
chrom() {
  local c=$1 v t0 ipid; v=$(vcf "$c"); t0=$(ts)
  # (b) alongside (a): the truth -- 1000G's own INFO AC/AN and super-population AFs for the same
  #     records, read from the VCF text by awk, never by PLINK
  ( zcat "$v" | cut -f1,2,4,5,8 | awk -F'\t' '
    /^#/ {next}
    $3 ~ /^[ACGT]$/ && $4 ~ /^[ACGT]$/ {
      n=split($5, kv, ";"); delete a
      for (i=1;i<=n;i++) { j=index(kv[i],"="); if (j) a[substr(kv[i],1,j-1)]=substr(kv[i],j+1) }
      print $1":"$2":"$3":"$4, a["AC"], a["AN"], a["AFR_AF"], a["AMR_AF"], a["EAS_AF"], a["EUR_AF"], a["SAS_AF"]
    }' > "info$c.txt" ) &
  ipid=$!
  # (a) PLINK's import: biallelic ACGT SNPs only, REF kept as A2 so A1 is always ALT
  $P --vcf "$v" --double-id --snps-only just-acgt --biallelic-only strict --make-bed --out "c$c" > /dev/null 2>&1
  # unique, allele-bearing IDs (1000G has duplicated rsIDs and '.')
  awk 'BEGIN{OFS="\t"}{$2=$1":"$4":"$6":"$5; print}' "c$c.bim" > "c$c.bim.new" && mv "c$c.bim.new" "c$c.bim"
  # (c) PLINK's counts: global with missingness, and per super-population
  $P --bfile "c$c" --freq counts --missing --out "f$c" > /dev/null 2>&1
  $P --bfile "c$c" --freq --within superpop.clst --out "s$c" > /dev/null 2>&1
  # (d) QC: MAF >= 1% (the GWAS set), then HWE pooled vs within EUR
  $P --bfile "c$c" --maf 0.01 --make-bed --out "q$c" > /dev/null 2>&1
  rm -f "c$c.bed"                  # built by this task; the QC'd bed carries on
  $P --bfile "q$c" --hwe 1e-6 --write-snplist --out "hp$c" > /dev/null 2>&1
  $P --bfile "q$c" --keep eur.keep --hwe 1e-6 --write-snplist --out "he$c" > /dev/null 2>&1
  # (e) PCA input: MAF >= 5%, LD-pruned
  $P --bfile "q$c" --maf 0.05 --indep-pairwise 50 5 0.2 --out "ld$c" > /dev/null 2>&1
  $P --bfile "q$c" --extract "ld$c.prune.in" --make-bed --out "pr$c" > /dev/null 2>&1
  wait "$ipid"

  # every record PLINK kept is the same record, in the same order, as awk's ACGT-biallelic read
  # of the text; C1 (A1 = ALT) == INFO AC and C1 + C2 == INFO AN, exactly
  local g p m q
  g=$(awk -v info="info$c.txt" 'FNR==1{next}
       {k++; if ((getline line < info) <= 0) {short++; next}; split(line, f, " ")
        if ($2!=f[1]) badid++; if ($5!=f[2]) badac++; if ($5+$6!=f[3]) badan++}
       END{n=k-short; while ((getline line < info) > 0) n++
           printf "%d %d %d %d %d", n, k, badid+0, badac+0, badan+0}' "f$c.frq.counts")
  # per super-population: round(AF_pop * NCHROBS) == PLINK's ALT count there (1000G prints AF
  # to 4 decimals and the largest population has 1322 chromosomes, so rounding moves AF*N by
  # <= 0.066 and the count is recovered exactly); the five counts sum to the global AC.
  # .frq.strat: CHR SNP CLST A1 A2 MAF MAC NCHROBS, MAC being the A1 (= ALT) count, five rows
  # per SNP. Grouped by position, not by ID: 1000G repeats a few records verbatim, so two
  # adjacent SNPs can share an ID, and grouping on an ID change misaligns everything after it.
  p=$(awk -v info="info$c.txt" 'FNR==1{next}
       (FNR-2)%5==0 { if (m && tot!=ac) badsum++
                 if ((getline line < info) <= 0) line=""; split(line, f, " ")
                 ac=f[2]; tot=0
                 af["AFR"]=f[4]; af["AMR"]=f[5]; af["EAS"]=f[6]; af["EUR"]=f[7]; af["SAS"]=f[8] }
       {if ($2!=f[1]) bad++; cnt=$7; tot+=cnt; want=int(af[$3]*$8+0.5); if (cnt!=want) bad++; m++}
       END{if (m && tot!=ac) badsum++; printf "%d %d %d", m, bad+0, badsum+0}' "s$c.frq.strat")
  # observation: adjacent records sharing CHR:POS:REF:ALT (exact repeats in the release)
  awk 'p==$2{print $2} {p=$2}' "c$c.bim" > "dup$c.txt"
  # missingness: rows, SNPs with any missing call, samples with any missing call
  m="$(awk 'NR>1{n++; if ($3>0) b++} END{printf "%d %d", n, b+0}' "f$c.lmiss") $(awk 'NR>1 && $4>0{b++} END{print b+0}' "f$c.imiss")"
  # MAF >= 0.01 keeps exactly the SNPs with min(AC, AN-AC)/AN >= 0.01 (AN = 5008: no tie at
  # the bound). The QC'd .bim is a subsequence of the text's order, so walk both together.
  q=$(awk -v q="q$c.bim" 'BEGIN{if ((getline l < q) > 0) {split(l, b, "\t"); nx=b[2]} else nx=""}
    {mn=($2<$3-$2)?$2:$3-$2; keep=(mn/$3 >= 0.01); inq=($1==nx); k+=keep; nq+=inq
     if (inq) {if ((getline l < q) > 0) {split(l, b, "\t"); nx=b[2]} else nx=""}
     if (keep!=inq) dis++}
    END{printf "%d %d %d", k, nq, dis+0}' "info$c.txt")
  echo "$c $g $p $m $q $(wc -l < "hp$c.snplist") $(wc -l < "he$c.snplist") $(wc -l < "pr$c.bim")" > "id.$c"
  rm -f "info$c.txt" "f$c.frq.counts" "s$c.frq.strat" "f$c.lmiss" "f$c.imiss"
  echo "$c $(( $(ts) - t0 ))" >> chrom-wall.txt
}
T1=$(ts)
pool chrom $CHROMS
rec wall_chrom_phase_s $(( $(ts) - T1 ))

# ---------- 2. the identities, summed over chromosomes -----------------------------------------
# id.<c>: 1 chr | 2 ninfo 3 nplink 4 badid 5 badac 6 badan | 7 poprows 8 badpop 9 badsum |
#         10 lmiss_rows 11 snps_missing 12 samples_missing | 13 maf_recount 14 maf_plink
#         15 maf_disagree | 16 hwe_pass_pooled 17 hwe_pass_eur | 18 pruned
for c in $CHROMS; do cat "id.$c" 2>/dev/null || echo "$c MISSING"; done > id-all.txt
NCH=$(awk 'NF==18' id-all.txt | wc -l | tr -d ' ')
[ "$NCH" = "$(echo $CHROMS | wc -w | tr -d ' ')" ] && must ok every_chromosome_reported || must no every_chromosome_reported "$NCH"
NINFO=$(sum 2 < id-all.txt); NPLK=$(sum 3 < id-all.txt)
rec snps_imported "$NPLK"
rec snps_acgt_biallelic_in_vcf_text "$NINFO"
[ "$NPLK" -gt 0 ] && [ "$NPLK" = "$NINFO" ] && [ "$(sum 4 < id-all.txt)" = 0 ] && must ok same_records || must no same_records "plink $NPLK text $NINFO"
[ "$NPLK" -gt 0 ] && [ "$(sum 5 < id-all.txt)" = 0 ] && must ok alt_count_eq_INFO_AC || must no alt_count_eq_INFO_AC "$(sum 5 < id-all.txt)"
[ "$NPLK" -gt 0 ] && [ "$(sum 6 < id-all.txt)" = 0 ] && must ok allele_number_eq_INFO_AN || must no allele_number_eq_INFO_AN "$(sum 6 < id-all.txt)"
NPOP=$(sum 7 < id-all.txt)
rec superpop_rows "$NPOP"
[ "$NPOP" = $((NPLK*5)) ] && [ "$(sum 8 < id-all.txt)" = 0 ] && must ok superpop_count_eq_INFO_AF || must no superpop_count_eq_INFO_AF "$(sum 8 < id-all.txt) of $NPOP"
[ "$NPOP" -gt 0 ] && [ "$(sum 9 < id-all.txt)" = 0 ] && must ok superpops_sum_to_AC || must no superpops_sum_to_AC "$(sum 9 < id-all.txt)"
# the integrated callset is complete, so every SNP and every sample is at 0 missing
[ "$(sum 10 < id-all.txt)" = "$NPLK" ] && [ "$(sum 11 < id-all.txt)" = 0 ] && [ "$(sum 12 < id-all.txt)" = 0 ] \
  && must ok zero_missingness || must no zero_missingness "$(awk '{a+=$11; b+=$12} END{print a, b}' id-all.txt)"
NQ=$(sum 14 < id-all.txt)
rec snps_maf01 "$NQ"
[ "$NQ" -gt 0 ] && [ "$NQ" = "$(sum 13 < id-all.txt)" ] && [ "$(sum 15 < id-all.txt)" = 0 ] \
  && must ok maf_filter_same_snps || must no maf_filter_same_snps "plink $NQ recount $(sum 13 < id-all.txt) disagree $(sum 15 < id-all.txt)"
rec hwe_fail_pooled_2504 $((NQ - $(sum 16 < id-all.txt)))
rec hwe_fail_within_EUR_503 $((NQ - $(sum 17 < id-all.txt)))
NPR=$(sum 18 < id-all.txt)
rec snps_ld_pruned "$NPR"

# ---------- 3. PCA on the pruned genome ------------------------------------------------------
: > merge.txt; first=""
for c in $CHROMS; do if [ -z "$first" ]; then first="pr$c"; else echo "pr$c" >> merge.txt; fi; done
T2=$(ts)
if [ -s merge.txt ]; then plink --keep-allele-order --memory 8000 --bfile "$first" --merge-list merge.txt --make-bed --out pruned > /dev/null 2>&1
else for e in bed bim fam; do cp "$first.$e" "pruned.$e"; done; fi
# the one multi-threaded step: give PLINK and its BLAS the whole box
OPENBLAS_NUM_THREADS=$NCPU OMP_NUM_THREADS=$NCPU MKL_NUM_THREADS=$NCPU \
  plink --bfile pruned --pca 10 --threads "$NCPU" --memory 16000 --out pca > pca.out 2>&1
cp pca.log "$OUT/pca.log"
rec wall_pca_s $(( $(ts) - T2 ))
# orthogonality of the 10 eigenvectors, as printed: max |cos| over the 45 pairs. At 6 printed
# significant figures, rounding alone moves a cosine by <~1e-6, so 1e-5 is the print's bound
read ORTH NRMLO NRMHI <<< "$(awk '{for(i=3;i<=12;i++) v[NR,i-2]=$i; n=NR}
  END{for(a=1;a<=10;a++){d=0; for(r=1;r<=n;r++) d+=v[r,a]*v[r,a]; nr[a]=sqrt(d)}
      m=0; for(a=1;a<=10;a++) for(b=a+1;b<=10;b++){d=0; for(r=1;r<=n;r++) d+=v[r,a]*v[r,b]; d/=nr[a]*nr[b]; if(d<0)d=-d; if(d>m)m=d}
      lo=nr[1]; hi=nr[1]; for(a=2;a<=10;a++){if(nr[a]<lo)lo=nr[a]; if(nr[a]>hi)hi=nr[a]}
      printf "%.3e %.6f %.6f\n", m, lo, hi}' pca.eigenvec)"
rec pca_max_abs_cos "$ORTH"
rec pca_vector_norms "$NRMLO..$NRMHI"
awk -v e="$ORTH" 'BEGIN{exit !(e+0 < 1e-5 && e==e)}' && must ok pca_orthogonal || must no pca_orthogonal "$ORTH"
rec pca_eigenvalues "$(tr '\n' ' ' < pca.eigenval)"
# descending order (definition of the decomposition)
awk 'NR>1 && $1>p{b++} {p=$1} END{exit b+0}' pca.eigenval && must ok eigenvalues_descending || must no eigenvalues_descending ""
# observation: per super-population PC1/PC2 centroids
awk 'NR==FNR{sp[$1]=$3; next} {s1[sp[$1]]+=$3; s2[sp[$1]]+=$4; k[sp[$1]]++}
     END{for (p in k) printf "%s:%.4f,%.4f ", p, s1[p]/k[p], s2[p]/k[p]; print ""}' superpop.clst pca.eigenvec > pc-centroids.txt
rec pc12_centroids "$(cat pc-centroids.txt)"

# ---------- 4. association: a planted effect plus an ancestry confound ----------------------
# y = 10 + 1.0 * dosage(CAUSAL) + 1.5 * [AFR] + noise; noise is a staged, pinned N(0,1) draw,
# so the phenotype is the same bytes on any chip (awk's rand() is not: it differs per image)
CAUSAL="${CAUSAL:?}"; CC=${CAUSAL%%:*}
$P --bfile "q$CC" --snp "$CAUSAL" --recode A --out causal > /dev/null 2>&1
awk 'NR==FNR{sp[$1]=$3; next} FILENAME==ARGV[2]{nz[$1]=$3; next}
     FNR==1{next} {printf "%s %s %.6f\n", $1, $2, 10 + 1.0*$7 + 1.5*(sp[$1]=="AFR") + nz[$1]}' \
     superpop.clst "$IN/noise.txt" causal.raw > pheno.txt
NPH=$(wc -l < pheno.txt | tr -d ' ')
[ "$NPH" = 2504 ] && must ok phenotype_built || must no phenotype_built "$NPH rows"
awk '{print $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12}' pca.eigenvec > pcs.txt
assoc() {
  local c=$1
  # .assoc.linear: CHR SNP BP A1 TEST NMISS BETA STAT P  ->  T^2, ID, P
  $P --bfile "q$c" --pheno "$W/pheno.txt" --covar "$W/pcs.txt" --linear hide-covar --out "a$c" > /dev/null 2>&1
  awk 'NR>1 && $8!="NA"{print $8*$8, $2, $9}' "a$c.assoc.linear" > "a$c.chisq"
  awk 'NR>1{n++} NR>1 && $8=="NA"{print $2 > "/dev/stderr"} END{print n+0}' "a$c.assoc.linear" > "a$c.rows" 2> "a$c.na"
  $P --bfile "q$c" --pheno "$W/pheno.txt" --linear --out "u$c" > /dev/null 2>&1
  awk 'NR>1 && $8!="NA"{print $8*$8, $2, $9}' "u$c.assoc.linear" > "u$c.chisq"
  rm -f "a$c.assoc.linear" "u$c.assoc.linear"
}
T3=$(ts)
pool assoc $CHROMS
rec wall_assoc_s $(( $(ts) - T3 ))
for c in $CHROMS; do cat "a$c.chisq"; done | sort -gr -k1,1 > adj.ranked
for c in $CHROMS; do cat "u$c.chisq"; done | sort -gr -k1,1 > unadj.ranked
NA=$(wc -l < adj.ranked | tr -d ' ')
NROWS=$(for c in $CHROMS; do cat "a$c.rows"; done | sum 1)
for c in $CHROMS; do cat "a$c.na"; done > "$OUT/assoc-na.txt"
for c in $CHROMS; do cat "dup$c.txt"; done > "$OUT/repeated-records.txt"
rec repeated_records "$(wc -l < "$OUT/repeated-records.txt" | tr -d ' ')"
rec snps_tested_with_stat "$NA"
rec snps_stat_NA "$(wc -l < "$OUT/assoc-na.txt" | tr -d ' ') ($(tr '\n' ' ' < "$OUT/assoc-na.txt"))"
[ "$NROWS" -gt 0 ] && [ "$NROWS" = "$NQ" ] && must ok every_qc_snp_has_a_row || must no every_qc_snp_has_a_row "$NROWS of $NQ"
# genomic inflation, lambda = median(T^2) / 0.454936, read off the ranked (descending) list
lam() { awk -v n="$(wc -l < "$1")" 'NR==int((n+1)/2){a=$1} NR==int(n/2)+1{b=$1} END{printf "%.4f\n", (n%2?a:(a+b)/2)/0.454936}' "$1"; }
rec lambda_gc_pc_adjusted "$(lam adj.ranked)"
rec lambda_gc_unadjusted "$(lam unadj.ranked)"
rec planted "$CAUSAL"
rec adj_top_hit_t2_p "$(awk 'NR==1{print $2, $1, $3}' adj.ranked)"
rec adj_best_other_t2_p "$(awk -v c="$CAUSAL" '$2!=c{print $2, $1, $3; exit}' adj.ranked)"
rec unadj_top_hit_t2_p "$(awk 'NR==1{print $2, $1, $3}' unadj.ranked)"
rec unadj_rank_of_planted "$(awk -v c="$CAUSAL" '$2==c{print NR; exit}' unadj.ranked)"
# nothing beats the planted SNP once ancestry is adjusted for (a tie with a perfect proxy is allowed)
awk -v c="$CAUSAL" '$2==c{cs=$1} $2!=c && $1>best{best=$1} END{exit !(cs>0 && cs>=best)}' adj.ranked \
  && must ok planted_snp_unbeaten_adjusted || must no planted_snp_unbeaten_adjusted "see adj_top_hit"

# ---------- 5. stage out ----------------------------------------------------------------------
cp pca.eigenvec pca.eigenval pheno.txt chrom-wall.txt id-all.txt "$OUT/"
awk 'NR<=1000' adj.ranked > "$OUT/adj.top1000"; awk 'NR<=1000' unadj.ranked > "$OUT/unadj.top1000"
for c in $CHROMS; do cat "c$c.log"; done > "$OUT/import.logs"
rec failures "$FAILS"
if [ "$FAILS" = 0 ]; then echo "PLINK REAL OK"; exit 0; else echo "PLINK REAL FAILED: $FAILS"; exit 1; fi
