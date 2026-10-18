#!/usr/bin/env bash
# PLINK 1.9 on the same 1000 Genomes VCF bytes as p2.sh, and the cross-code comparison:
# per-variant allele counts and HWE P genome-wide, exact-PCA eigenvalues, and the planted-SNP
# regressions on PLINK 2's own phenotype and PC covariates.
#
#   p19.sh <parallel chromosomes> [chromosomes...]     (default 1..22)
#   p19.sh chrom <N>                                   (one chromosome; called by the first form)
#
# Inputs in /tmp: the VCFs, vcf.sha256, and p2.sh's hand-off (p2.counts.tsv.gz, dup.txt, prune.prune.in,
# causal.txt, pheno.txt, covar.txt, pcax.eigenval, glm.causal.tsv). Awk only, and POSIX awk:
# bioconda's x86 image has a BusyBox userland.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
cd /tmp

if [ "${1:-}" = chrom ]; then
  # recipes/plink's import (--snps-only just-acgt --biallelic-only strict); --keep-allele-order
  # so A1 stays ALT and A2 REF, which makes CHR:POS:A2:A1 the ID PLINK 2 assigned.
  c="$2"; o="c$c"; t0=$(date +%s)
  plink --vcf ALL.chr$c.phase3_*.vcf.gz --keep-allele-order --double-id --snps-only just-acgt \
        --biallelic-only strict --memory 3000 --threads 1 --make-bed --out "$o.raw" > "log.p19_chr$c.txt" 2>&1 || exit 1
  awk 'BEGIN{OFS="\t"}{$2=$1":"$4":"$6":"$5; print}' "$o.raw.bim" > "$o.raw.bim.new" && mv "$o.raw.bim.new" "$o.raw.bim"
  X=""; test -s dup.txt && X="--exclude dup.txt"   # the repeated CHR:POS:REF:ALT records PLINK 2 dropped
  plink --bfile "$o.raw" --keep-allele-order $X --freq counts --hardy --memory 3000 --threads 1 --out "$o" >> "log.p19_chr$c.txt" 2>&1 || exit 1
  plink --bfile "$o.raw" --keep-allele-order --extract prune.prune.in --make-bed --memory 3000 --threads 1 --out "pr$c" >> "log.p19_chr$c.txt" 2>&1 || exit 1
  rm -f "$o.raw.bed" "$o.raw.bim"   # 49 GB of .bed genome-wide, in tmpfs; only counts and the pruned subset stay
  # .frq.counts: CHR SNP A1 A2 C1 C2 G0  ->  ID, minor count, total.   .hwe: ALL rows only.
  awk 'NR>1{t=$5+$6; print $2 "\t" ($5<$6?$5:$6) "\t" t}' "$o.frq.counts" > "$o.minor.tsv"
  awk 'NR>1 && $3 ~ /^ALL/{print $NF}' "$o.hwe" > "$o.p.tsv"
  rm -f "$o.frq.counts" "$o.hwe"
  echo "chr$c $(( $(date +%s) - t0 ))s" >> chrom.times
  exit 0
fi

P="${1:?parallel chromosomes}"; shift
CHR="${*:-$(seq 1 22)}"
: > score.tsv
rec()  { printf '%s\t%s\n' "$1" "$2" | tee -a score.tsv; }
fail() { echo "FAIL: $*" | tee -a score.tsv; tar cf logs19.tar log.*.txt 2>/dev/null; exit 1; }
now()  { date +%s; }
T0=$(now)

sha256sum -c vcf.sha256 > /dev/null || fail "a VCF or the panel does not match its sha256"
rec plink_version "$(plink --version 2>&1 | awk 'NR==1')"
case "$(uname -m)" in aarch64) WANT=183 ;; x86_64) WANT=62 ;; *) WANT=unknown ;; esac
EM=$(od -An -tu1 -j18 -N2 "$(command -v plink)" | awk '{print $1 + $2*256}')
test "$EM" = "$WANT" || fail "plink e_machine $EM on $(uname -m)"
rec arch "$(uname -m) e_machine=$EM nproc=$(nproc)"

# Largest chromosomes first, so the long poles start at once.
t=$(now); : > chrom.times
# A bash job pool rather than xargs -P, which BusyBox may lack; each chromosome leaves a marker.
for c in $(for c in $CHR; do printf '%s %s\n' "$(wc -c < ALL.chr$c.phase3_*.vcf.gz)" "$c"; done | sort -rn | awk '{print $2}'); do
  while [ "$(jobs -rp | wc -l)" -ge "$P" ]; do wait -n; done
  ( bash /tmp/p19.sh chrom "$c" && : > "done.$c" ) &
done
wait
for c in $CHR; do test -e "done.$c" || fail "chr$c failed: $(tail -5 log.p19_chr$c.txt)"; done
rec wall_p19_import_counts "$(( $(now) - t ))"
rec p19_chrom_seconds "$(sort -k2 -n chrom.times | tr '\n' ' ')"

# ---------------------------------------------- 1. per-variant counts, all variants, exact
cat19() { for c in $CHR; do cat "c$c.$1.tsv"; done; }
gzip -dc p2.counts.tsv.gz | paste - <(cat19 minor) <(cat19 p) | awk '
  { n++
    if ($1 != $5) { bi++; if (ex++ < 3) print "example\tid " $1 " vs " $5 > "/dev/stderr" }
    if ($2 != $6) { bm++; if (ex++ < 3) print "example\tminor " $1 " " $2 " vs " $6 > "/dev/stderr" }
    if ($3 != $7) bt++
    # HWE: 1.9 prints 4 significant figures (<= 5e-4 relative), PLINK 2 six (<= 5e-6).
    a = $4 + 0; b = $8 + 0; m = (a > b ? a : b); d = a - b; if (d < 0) d = -d
    r = (m > 0) ? d / m : 0; if (r > wr) wr = r
    if (d > wd) wd = d
    if (r > 5.1e-4) bh++
    if (a < 1e-300 || b < 1e-300) tiny++
  }
  END { printf "%d %d %d %d %d %.3e %.3e %d\n", n, bi+0, bm+0, bt+0, bh+0, wd, wr, tiny+0 }' > cmp.txt
read -r N BI BM BT BH WD WR TINY < cmp.txt
NP2=$(gzip -dc p2.counts.tsv.gz | awk 'END{print NR}'); N19=$(cat19 minor | awk 'END{print NR}')
rec variants_p2_p19 "$NP2 $N19"
test "$NP2" = "$N19" || fail "PLINK 2 kept $NP2 variants, PLINK 1.9 kept $N19"
test "$BI" = 0 || fail "$BI rows where the two variant lists differ"
rec same_variant_set "$N of $N, same IDs in the same order"
test "$BM" = 0 -a "$BT" = 0 || fail "$BM minor-count / $BT total mismatches"
rec identity_minor_and_total_counts "$N of $N exact"
rec hwe_max_abs_diff__max_rel__n_below_1e-300 "$WD $WR $TINY"
test "$BH" = 0 || fail "$BH HWE P-values differ by more than printed precision"
rec agreement_hwe "$N of $N within 5.1e-4 relative"

# ---------------------------------------------- 2. exact PCA on PLINK 2's pruned set
t=$(now)
for c in $CHR; do echo "pr$c"; done | awk 'NR>1' > mlist.txt
FIRST=$(echo $CHR | awk '{print "pr"$1}')
plink --bfile "$FIRST" --merge-list mlist.txt --keep-allele-order --make-bed --out pr > log.p19_merge.txt 2>&1 || fail "merge"
NPR=$(awk 'END{print NR}' pr.bim)
test "$NPR" = "$(awk 'END{print NR}' prune.prune.in)" || fail "pruned set: $NPR in 1.9, $(awk 'END{print NR}' prune.prune.in) in PLINK 2"
plink --bfile pr --keep-allele-order --pca 10 --threads "$(nproc)" --out pca19 > log.p19_pca.txt 2>&1 || fail "pca"
rec wall_p19_pca "$(( $(now) - t ))"
# Same GRM, same eigenproblem, a fixed point: both print 6 significant figures, so two roundings
# bound the difference at 1e-5 relative.
paste pca19.eigenval pcax.eigenval | awk '{d=($1-$2)/$2; d=d<0?-d:d; if(d>m)m=d} END{printf "%.3e\n", m}' > ev.txt
rec eigenval_p19_exact_vs_p2_exact_max_rel "$(cat ev.txt)"
awk -v m="$(cat ev.txt)" 'BEGIN{exit !(m+0 <= 1e-5)}' || fail "exact-PCA eigenvalues differ beyond printed precision"

# ---------------------------------------------- 3. the planted SNPs, PLINK 2's phenotype and PCs
# --linear is the same OLS as --glm. PLINK 2 standardized its covariates and 1.9 did not; a SNP's
# coefficient is invariant to an affine rescaling of the other regressors, so the two must agree.
# --allow-no-sex: the VCFs carry no sex, and without it 1.9 drops every phenotype and skips
# --linear with only a warning (measured). PLINK 2 has no such rule.
plink --bfile pr --keep-allele-order --extract causal.txt --pheno pheno.txt --pheno-name Y \
      --covar covar.txt --allow-no-sex --linear hide-covar --out lin > log.p19_linear.txt 2>&1 || fail "linear"
# 1.9 .assoc.linear: CHR SNP BP A1 TEST NMISS BETA STAT P (4 significant figures)
awk 'FILENAME=="glm.causal.tsv"{ if(FNR==1){for(i=1;i<=NF;i++){h=$i;sub(/^#/,"",h);c[h]=i}; next}
                                 a[$(c["ID"])]=$(c["A1"]); b[$(c["ID"])]=$(c["BETA"]); t[$(c["ID"])]=$(c["T_STAT"]); next }
     FNR==1{next}
     { id=$2; s=($4==a[id])?1:-1; n++
       rb=($7*s-b[id])/b[id]; rb=rb<0?-rb:rb; rt=($8*s-t[id])/t[id]; rt=rt<0?-rt:rt
       if(rb>mb)mb=rb; if(rt>mt)mt=rt; if(s<0) fl++ }
     END{ printf "%d %.3e %.3e %d\n", n, mb, mt, fl+0 }' glm.causal.tsv lin.assoc.linear > lin.txt
read -r LN LB LT LF < lin.txt
rec linear_vs_glm_n__beta_max_rel__t_max_rel__a1_flips "$LN $LB $LT $LF"
test "$LN" = "$(awk 'END{print NR}' causal.txt)" || fail "--linear tested $LN of the causal SNPs"
awk -v b="$LB" -v t="$LT" 'BEGIN{exit !(b+0 <= 5.1e-4 && t+0 <= 5.1e-4)}' || fail "--linear and --glm disagree beyond printed precision"

tar cf logs19.tar log.*.txt *.log
rec wall_task "$(( $(now) - T0 ))"
PEAK=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes 2>/dev/null || echo 0)
rec peak_mem_gib "$(awk -v p="$PEAK" 'BEGIN{printf "%.2f", p/1073741824}')"
rec tmp_used_gib "$(df -k /tmp | awk 'NR==2{printf "%.1f", $3/1048576}')"
echo "PLINK19 CROSSCHECK OK"
