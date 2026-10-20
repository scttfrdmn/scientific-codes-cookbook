#!/usr/bin/env bash
# raxml-ng-real, task 1 (RAxML-NG image): a gene-family tree the way one is built -- adaptive ML
# search under LG+G4, nonparametric bootstrap, supports on the best tree -- then the checks.
#
#   bash real.sh <alignment.fa> <threads> <bootstrap replicates>
#
# Threads and seed are pinned (--threads T --workers 1 --seed 12345): the thread count feeds the
# adaptive heuristic's start-tree choice, so AUTO would change which optimum the search lands on.
# Everything lands in /tmp/out/. Runs on the aarch.bio image and on bioconda's (BusyBox) x86 one:
# awk and coreutils only, no python, no join/numfmt/perl.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
ALN=$1 T=$2 B=$3
O=/tmp/out
mkdir -p "$O"
cd /tmp

{
  echo "uname_m $(uname -m)"
  echo "online_cpus $(grep -c '^processor' /proc/cpuinfo)"
  echo "physical_cores $(cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list | sort -u | wc -l)"
  awk -F': *' '/^model name|^CPU part|^CPU implementer/{print $1": "$2}' /proc/cpuinfo | sort | uniq -c
  echo "mem_total_kib $(awk '/^MemTotal/{print $2}' /proc/meminfo)"
  echo "raxml-ng $(command -v raxml-ng)"
  raxml-ng --version 2>&1 | grep -m1 'RAxML-NG v'
} > "$O/host.txt" 2>&1
awk '/^online_cpus/{c=$2} /^physical_cores/{p=$2} END{printf "threads_per_core %d\n", (p>0)?c/p:0}' "$O/host.txt" >> "$O/host.txt"
cat "$O/host.txt"
grep -q 'RAxML-NG v. 2.0.2' "$O/host.txt" || { echo "FAIL: not RAxML-NG 2.0.2"; exit 1; }

# The alignment's shape, computed independently of RAxML-NG: distinct site patterns (gap and
# undetermined characters folded together, as RAxML-NG encodes them), gap fraction, invariant sites.
awk '/^>/{n++; next} {s[n]=s[n] $0}
END{ L=length(s[1]); g=0; inv=0
  for (j=1;j<=L;j++) { c=""; f=""; v=1; allg=1
    for (i=1;i<=n;i++) { ch=substr(s[i],j,1)
      if (ch ~ /[-?X.]/) { g++; c=c "-"; continue }
      allg=0; c=c ch
      if (f=="") f=ch; else if (ch!=f) v=0 }
    if (!allg) seen[c]=1; if (v) inv++ }
  np=0; for (k in seen) np++
  printf "taxa %d\nsites %d\npatterns %d\ngaps_pct %.2f\ninvariant_pct %.2f\n", n, L, np, 100*g/(n*L), 100*inv/L }' \
  "$ALN" > "$O/aln-shape.txt"
cat "$O/aln-shape.txt"

FAILS=0
phase() {
  name=$1; shift
  echo "== $name $(date -u +%H:%M:%S)"
  s=$(date +%s)
  raxml-ng "$@" --msa "$ALN" --threads "$T" --workers 1 --seed 12345 --redo > "$O/$name.stdout" 2>&1
  rc=$?
  printf '%s\t%s s\trc %s\n' "$name" "$(( $(date +%s) - s ))" "$rc" | tee -a "$O/times.txt"
  [ "$rc" = 0 ] || FAILS=$((FAILS+1))
}
phase search    --search --model LG+G4 --prefix rx
phase bootstrap --bootstrap --model LG+G4 --bs-trees "$B" --prefix bs
# Reported, not asserted: whether this many replicates passes RAxML-NG's own MRE convergence test.
raxml-ng --bsconverge --bs-trees bs.raxml.bootstraps --prefix bc --seed 12345 --threads 1 --redo > "$O/bsconverge.stdout" 2>&1
echo "bsconverge rc $?" >> "$O/times.txt"
phase support   --support --tree rx.raxml.bestTree --bs-trees bs.raxml.bootstraps --bs-metric fbp,tbe --prefix sup
# The likelihood at the search's own optimum, nothing optimised, must be the value it reported; and
# re-optimising from there must not climb, or it was not the maximum.
ALPHA=$(awk -F'alpha: ' '/Rate heterogeneity: GAMMA/{split($2,a," "); print a[1]}' rx.raxml.log)
phase atpoint   --evaluate --tree rx.raxml.bestTree --model rx.raxml.bestModel --opt-model off --opt-branches off --prefix pt
phase reopt     --evaluate --tree rx.raxml.bestTree --model LG+G4 --lh-epsilon 0.001 --prefix op

awk '/^(anon|file) /{print "cgroup_"$1, $2}' /sys/fs/cgroup/memory.stat 2>/dev/null >> "$O/host.txt"
echo "cgroup_memory_peak $(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$O/host.txt"
grep -m1 'SIMD kernels' rx.raxml.log | sed 's/^ */raxml_/' >> "$O/host.txt"

NTAX=$(awk '/^taxa/{print $2}' "$O/aln-shape.txt")
tips() { grep -oE '[(,][A-Za-z0-9_]+:' "$1" | wc -l | tr -d ' '; }
semis() { grep -c ';' "$1" 2>/dev/null || echo 0; }
# Supports sit on internal nodes as `)<value>:`; an unrooted binary tree has n-3 internal branches.
supports() { grep -oE '\)[0-9.]+:' "$1" | tr -d '):' ; }
supports sup.raxml.supportFBP > fbp.txt
supports sup.raxml.supportTBE > tbe.txt
{
  echo "nsearch $(grep -c 'ML tree search #' rx.raxml.log)"
  echo "nmltrees $(semis rx.raxml.mlTrees)"
  echo "nstart $(semis rx.raxml.startTree)"
  echo "ntips_best $(tips rx.raxml.bestTree)"
  echo "nboot $(semis bs.raxml.bootstraps)"
  echo "nboot_full $(awk -v n="$NTAX" '{c=gsub(/[(,][A-Za-z0-9_]+:/,"&"); if (c==n) k++} END{print k+0}' bs.raxml.bootstraps)"
  echo "nfbp $(wc -l < fbp.txt | tr -d ' ')"
  echo "nfbp_in_range $(awk '$1>=0 && $1<=100 && $1==int($1){k++} END{print k+0}' fbp.txt)"
  echo "ntbe $(wc -l < tbe.txt | tr -d ' ')"
  echo "ntbe_in_range $(awk '$1>=0 && $1<=1{k++} END{print k+0}' tbe.txt)"
  echo "tbe_ge_fbp $(paste fbp.txt tbe.txt | awk '$2*100 >= $1-0.5 {k++} END{print k+0}')"
  echo "fbp_mean $(awk '{s+=$1} END{if(NR) printf "%.4f", s/NR}' fbp.txt)"
  echo "tbe_mean $(awk '{s+=$1} END{if(NR) printf "%.6f", s/NR}' tbe.txt)"
  echo "finished $(grep -c '^Analysis started: .* / finished: ' rx.raxml.log)"
  echo "model_ok $(grep -cF 'LG+G4m{' rx.raxml.bestModel)"
  echo "sites_ok $(grep -cF "= 1-$(awk '/^sites/{print $2}' "$O/aln-shape.txt")" rx.raxml.bestModel)"
  echo "L_point $(awk '/final logLikelihood:/{print $NF}' pt.raxml.log)"
  echo "L_reopt $(awk '/final logLikelihood:/{print $NF}' op.raxml.log)"
  echo "alpha $ALPHA"
} > counts.txt
cat counts.txt

cat "$O/aln-shape.txt" counts.txt rx.raxml.log | awk -v B="$B" '
/^taxa /           { ntax_in = $2 }   /^sites /     { sites_in = $2 }
/^patterns /       { patt_in = $2 }   /^gaps_pct /  { gaps_in = $2 }   /^invariant_pct / { inv_in = $2 }
/^nsearch /        { nsearch = $2 }   /^nmltrees /  { nml = $2 }       /^nstart /    { nst = $2 }
/^ntips_best /     { ntips = $2 }     /^nboot /     { nb = $2 }        /^nboot_full / { nbf = $2 }
/^nfbp /           { nfbp = $2 }      /^nfbp_in_range / { nfbpr = $2 }
/^ntbe /           { ntbe = $2 }      /^ntbe_in_range / { ntber = $2 }  /^tbe_ge_fbp / { tge = $2 }
/^finished /       { fin = $2 }       /^model_ok /  { mok = $2 }       /^sites_ok /  { sok = $2 }
/^L_point /        { Lpt = $2 }       /^L_reopt /   { Lop = $2 }
/^Final LogLikelihood:/         { L = $NF }
/^AIC score:/                   { aic = $3; aicc = $7; bic = $11 }
/^Free parameters/              { k = $NF }
/Loaded alignment with/         { ntax = $(NF-4); nsites = $(NF-1) }
/^Alignment sites \/ patterns:/ { npatt = $NF }
/^Gaps:/                        { gaps = $2 }
/^Invariant sites:/             { inv = $3 }
/ML tree search #/              { v = $NF + 0; if (best == "" || v > best) best = v }
function check(label, ok, detail) {
  printf "%-28s %-4s %s\n", label, ok ? "ok" : "FAIL", detail
  if (!ok) fail = fail (fail ? ", " : "") label
}
function abs(x) { return x < 0 ? -x : x }
END {
  check("taxa_read_by_raxml", ntax == ntax_in, ntax "  want " ntax_in " (taxa in the FASTA)")
  check("sites_read_by_raxml", nsites == sites_in, nsites "  want " sites_in)
  check("patterns", npatt == patt_in, npatt "  want " patt_in " (distinct columns, counted independently)")
  check("gaps_pct", gaps == gaps_in, gaps "%  want " gaps_in "%")
  check("invariant_pct", inv == inv_in, inv "%  want " inv_in "%")
  check("tips_in_best_tree", ntips == ntax_in, ntips "  want " ntax_in)
  check("free_parameters", k == 2 * ntax - 2, k "  want 2n-2 = " 2 * ntax - 2 " (2n-3 branches + alpha)")
  e = 2 * k - 2 * L
  check("aic_identity", abs(aic - e) < 1e-4, aic "  want 2k-2lnL = " sprintf("%.6f", e))
  e = k * log(nsites) - 2 * L
  check("bic_identity", abs(bic - e) < 1e-4, bic "  want k ln(n)-2lnL = " sprintf("%.6f", e))
  check("final_is_best_of_searches", abs(L - best) < 1e-6, L "  want the best of " nsearch " searches (" sprintf("%.6f", best) ")")
  check("ml_trees_vs_searches", nml == nsearch && nsearch > 0, nml "  want " nsearch)
  check("start_trees_vs_searches", nst == nsearch, nst "  want " nsearch)
  check("likelihood_at_own_point", Lpt != "" && abs(Lpt - L) < 1e-4, Lpt "  want " L " (evaluated, nothing optimised)")
  check("reopt_does_not_climb", Lop != "" && Lop - L < 0.01, Lop "  want <= " L " + 0.01 (nothing beats the maximum)")
  check("bootstrap_replicates", nb == B && nbf == B, nb " trees, " nbf " with all " ntax_in " tips  want " B)
  check("fbp_supports", nfbp == ntax_in - 3 && nfbpr == nfbp, nfbp " (" nfbpr " integer 0..100)  want n-3 = " ntax_in - 3)
  check("tbe_supports", ntbe == ntax_in - 3 && ntber == ntbe, ntbe " (" ntber " in 0..1)  want n-3 = " ntax_in - 3)
  check("tbe_ge_fbp", tge == nfbp, tge " of " nfbp "  want all (TBE >= FBP per branch, by definition; FBP is rounded to 1%)")
  check("raxml_reported_finished", fin == 1, fin "  want 1")
  check("model_is_LG+G4m", mok == 1, mok "  want 1")
  check("all_sites_in_partition", sok == 1, sok "  want 1")
  printf "best_log_likelihood          %s  (reported)\naicc                         %s  (reported; a valid criterion only when sites > k+1)\n", L, aicc
  if (fail != "") { print "FAILED: " fail; exit 1 }
  print "ALL CHECKS OK"
}' > "$O/checks.txt"
CRC=$?
cat "$O/checks.txt"
[ "$CRC" = 0 ] || FAILS=$((FAILS+1))

cp rx.raxml.log rx.raxml.bestTree rx.raxml.bestModel rx.raxml.mlTrees rx.raxml.startTree \
   bs.raxml.log bs.raxml.bootstraps sup.raxml.log sup.raxml.supportFBP sup.raxml.supportTBE \
   pt.raxml.log op.raxml.log bc.raxml.log counts.txt "$O/" 2>/dev/null
df -k /tmp | awk 'NR==2{print "tmp_used_kib", $3}' >> "$O/host.txt"
ls -la "$O"
echo "fails $FAILS"
[ "$FAILS" = 0 ] && echo "RAXML-NG-REAL OK"
exit "$FAILS"
