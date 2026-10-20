#!/usr/bin/env bash
# Task 2, in the iqtree image: IQ-TREE's likelihood at the same two fixed points MrBayes evaluated,
# then the converged consensus against both ML trees, a control, and Graviton4's consensus.
#
#   bash compare.sh          (inputs in /tmp; writes /tmp/out/)
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
O=/tmp/out; mkdir -p "$O"
F=0
fail() { echo "FAIL: $*" | tee -a "$O/checks.txt"; F=1; }
ok()   { echo "ok    $*" | tee -a "$O/checks.txt"; }
rec()  { printf '%s\t%s\n' "$1" "$2" | tee -a "$O/score.tsv"; }
get()  { awk -F'\t' -v k="$1" '$1==k{v=$2} END{print v}' mb.score.tsv; }
TOL=0.01   # MrBayes is single-precision: one float32 rounding of a 52,707-unit total is 3.1e-3

echo "== IQ-TREE at each fixed point: tree, branch lengths and alpha all held (-blfix) =="
A_IQ=$(get alpha_iqtree); A_RX=$(get alpha_raxml)
iqev() {  # $1 tag $2 tree $3 alpha
  iqtree3 -s aln.fa -te "$2" -m "LG+G4{$3}" -blfix -T 1 --prefix "ev_$1" -redo > "$O/ev_$1.stdout" 2>&1
  cp "ev_$1.iqtree" "$O/" 2>/dev/null
  awk '/^Log-likelihood of the tree:/{print $5}' "ev_$1.iqtree"
}
I_IQ=$(iqev iq iqout.treefile "$A_IQ"); I_RX=$(iqev rx rx.raxml.bestTree "$A_RX")
rec iqtree_lnl_at_iqtree_point "$I_IQ"; rec iqtree_lnl_at_raxml_point "$I_RX"
R_OWN=$(awk '/^Final LogLikelihood:/{print $3}' rx.raxml.log)
I_OWN=$(awk '/^Log-likelihood of the tree:/{print $5}' iqout.iqtree)
rec raxml_own_final_lnl "$R_OWN"; rec iqtree_own_ml_lnl "$I_OWN"
cmp_l() {  # $1 label $2 a $3 b
  d=$(awk -v a="$2" -v b="$3" 'BEGIN{d=a-b; if(d<0)d=-d; printf "%.6f", d}')
  rec "delta_$1" "$d"
  [ -n "$2" ] && [ -n "$3" ] && awk -v d="$d" -v t="$TOL" 'BEGIN{exit !(d+0 < t+0)}' \
    && ok "$1: |$2 - ($3)| = $d < $TOL" || fail "$1: $2 vs $3, |d| = $d"
}
cmp_l mrbayes_vs_iqtree_at_iqtree_point "$(get mb_lnl_at_iqtree_point)" "$I_IQ"
cmp_l mrbayes_vs_iqtree_at_raxml_point  "$(get mb_lnl_at_raxml_point)"  "$I_RX"
cmp_l mrbayes_vs_raxml_own_final        "$(get mb_lnl_at_raxml_point)"  "$R_OWN"
cmp_l iqtree_blfix_vs_its_own_ml        "$I_IQ" "$I_OWN"

echo "== nothing the sampler ever visited beats the ML point =="
# MrBayes reports the best state each cold chain reached, sampled or not. Under the same model it
# cannot exceed the maximum likelihood, here MrBayes' own value at IQ-TREE's point.
CEIL=$(get mb_lnl_at_iqtree_point)
awk '/Likelihood of best state for "cold" chain of run/{print $NF}' mb.out > best.txt
rec best_cold_state_lnl "$(tr '\n' ' ' < best.txt)"
[ "$(wc -l < best.txt | tr -d ' ')" = 2 ] && awk -v c="$CEIL" '$1+0 > c+0 {bad=1} END{exit bad}' best.txt \
  && ok "both cold chains' best states <= the ML point ($CEIL)" || fail "a visited state beats the ML point $CEIL"

echo "== the converged consensus against both ML trees, a control and Graviton4 =="
head -1 mb.consensus.nwk > mb.majrule.nwk
lab() { tr ',()' '\n\n\n' < "$1" | sed 's/:.*//' | grep -E '^[A-Za-z0-9_]+$' | sort -u; }
for f in mb.majrule.nwk iqout.treefile rx.raxml.bestTree; do lab "$f" > "$f.labels"; done
cmp -s mb.majrule.nwk.labels iqout.treefile.labels && cmp -s mb.majrule.nwk.labels rx.raxml.bestTree.labels \
  && ok "leaf sets identical across all three ($(grep -c . mb.majrule.nwk.labels))" || fail "leaf sets differ"
awk '{lab[++n]=$0} END{s="(" lab[1] "," lab[2] ")"; for(i=3;i<=n;i++) s="(" s "," lab[i] ")"; print s ";"}' \
  mb.majrule.nwk.labels > caterpillar.nwk
head -1 g4.consensus.nwk > g4.majrule.nwk
rf() {  # $1 label $2 tree A $3 tree B -> RF from IQ-TREE's matrix
  cat "$2" "$3" > "rf_$1.trees"
  iqtree3 -rf_all "rf_$1.trees" -redo > /dev/null 2>&1
  rec "rf_$1" "$(awk 'NR==2{print $3}' "rf_$1.trees.rfdist")"
}
rf mb_vs_iqtree       mb.majrule.nwk iqout.treefile
rf mb_vs_raxml        mb.majrule.nwk rx.raxml.bestTree
rf iqtree_vs_raxml    iqout.treefile rx.raxml.bestTree
rf mb_vs_caterpillar  mb.majrule.nwk caterpillar.nwk
rf mb_vs_graviton4_mb mb.majrule.nwk g4.majrule.nwk
RIQ=$(awk -F'\t' '$1=="rf_iqtree_vs_raxml"{print $2}' "$O/score.tsv")
[ "$RIQ" = 0 ] && ok "the two ML trees are one topology (RF 0): the calibration floor" || fail "ML trees RF $RIQ"

echo "== split support, from MrBayes' own .tstat =="
awk 'NR>1 && NF>2 && $1 ~ /^[0-9]+$/ {n++; if ($3+0 >= 0.95) s++; if ($3+0 > 0.5) m++} END{print "splits_listed\t" n "\nsplits_pp_gt_0.5\t" m "\nsplits_pp_ge_0.95\t" s}' mb.tstat | tee -a "$O/score.tsv"
TS_MAJ=$(awk -F'\t' '$1=="splits_pp_gt_0.5"{print $2}' "$O/score.tsv")

echo "== bipartitions, split by split (splits.awk, one 0/1 string per split) =="
for f in mb.majrule.nwk iqout.treefile rx.raxml.bestTree g4.majrule.nwk; do
  awk -v labels=mb.majrule.nwk.labels -f splits.awk "$f" | sort -u > "$f.splits" || fail "splits.awk on $f"
done
NIQ=$(wc -l < iqout.treefile.splits | tr -d ' '); NRX=$(wc -l < rx.raxml.bestTree.splits | tr -d ' ')
rec ml_splits "$NIQ / $NRX"
[ "$NIQ" = 111 ] && [ "$NRX" = 111 ] && ok "both ML trees are binary: 111 = n-3 splits each" || fail "ML split counts $NIQ / $NRX"
cut -d' ' -f1 iqout.treefile.splits > iq.set; cut -d' ' -f1 rx.raxml.bestTree.splits > rx.set
cmp -s iq.set rx.set && ok "IQ-TREE and RAxML-NG trees have the identical 111 splits" || fail "ML split sets differ"
NMB=$(wc -l < mb.majrule.nwk.splits | tr -d ' ')
rec consensus_splits "$NMB"
# The 50% majority-rule consensus is, by definition, exactly the splits with posterior > 0.5.
[ "$NMB" = "$TS_MAJ" ] && ok "majority-rule consensus has exactly the $TS_MAJ splits .tstat puts above 0.5" \
  || fail "consensus has $NMB splits, .tstat has $TS_MAJ above 0.5"
awk 'NR==FNR{ml[$1]=1; next} {pp=$2+0; if ($1 in ml) {sh++} else {ab++; if (pp>mx) mx=pp; if (pp>=0.95) ab95++}; if (pp>=0.95) n95++}
     END{printf "consensus_splits_in_ml_tree\t%d\nconsensus_splits_not_in_ml_tree\t%d\nmax_pp_of_split_not_in_ml\t%.3f\npp_ge_0.95_splits\t%d\npp_ge_0.95_splits_not_in_ml\t%d\n", sh, ab, mx, n95, ab95}' \
  iq.set mb.majrule.nwk.splits | tee -a "$O/score.tsv"
cut -d' ' -f1 mb.majrule.nwk.splits > mb.set; cut -d' ' -f1 g4.majrule.nwk.splits > g4.set
rec ml_splits_not_in_consensus "$(awk 'NR==FNR{c[$1]=1; next} !($1 in c){k++} END{print k+0}' mb.set iq.set)"
rec consensus_split_set_vs_graviton4 "$(cmp -s mb.set g4.set && echo identical || echo differs)"
rec consensus_pp_labels_vs_graviton4 "$(cmp -s mb.majrule.nwk.splits g4.majrule.nwk.splits && echo identical || echo differs)"
for f in *.rfdist; do echo "--- $f"; cat "$f"; done > "$O/rfdist.txt"
rec result "$([ $F = 0 ] && echo PASS || echo FAIL)"
exit $F
