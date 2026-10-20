#!/usr/bin/env bash
# raxml-ng-real, task 2 (IQ-TREE image): an unrelated code computes the LG+G4 likelihood of
# RAxML-NG's best tree, re-optimising branch lengths and alpha until it stops moving, and must
# land where RAxML-NG did.
#
#   bash iq.sh <alignment.fa> <threads>   (reads rx.raxml.bestTree + rx.raxml.log from task 1)
#
# The loop is iqtree-real's eval.sh, the same pinned bytes, unchanged: it re-runs `iqtree3 -te`
# from its own output until logL moves < 0.001 (one pass is not always the fixed point). The
# bound is 0.01, the recipe's: IQ-TREE prints logL to 4 decimals, so it is set by the print, not
# by how closely the codes happened to agree.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
ALN=$1 T=$2
O=/tmp/out
cd /tmp
cp rx.raxml.bestTree real.treefile
bash eval.sh "$ALN" "$T"
RC=$?
LR=$(awk '/^Final LogLikelihood:/{print $3}' rx.raxml.log)
AR=$(awk -F'alpha: ' '/Rate heterogeneity: GAMMA/{split($2,a," "); print a[1]}' rx.raxml.log)
LI=$(awk '/^Log-likelihood of the tree:/{print $5}' "$O/evconv.iqtree")
AI=$(awk '/^Gamma shape alpha:/{print $4}' "$O/evconv.iqtree")
L0=$(awk '/^Log-likelihood of the tree:/{print $5}' "$O/ev0.iqtree")
D=$(awk -v a="$LR" -v b="$LI" 'BEGIN{x=a-b; if(x<0)x=-x; printf "%.6f", x}')
{
  echo "eval_rc                     $RC   want 0 (EVAL OK: a fixed point, every tip, LG+G4)"
  echo "raxml_final_logL            $LR   (alpha $AR)"
  echo "iqtree_first_pass_logL      $L0"
  echo "iqtree_fixed_point_logL     $LI   (alpha $AI)"
  echo "abs_diff                    $D   want < 0.01"
} | tee "$O/crosscheck.txt"
[ "$RC" = 0 ] && [ -n "$LR" ] && [ -n "$LI" ] && awk -v d="$D" 'BEGIN{exit !(d<0.01)}' \
  && { echo "CROSSCHECK OK" | tee -a "$O/crosscheck.txt"; exit 0; }
echo "CROSSCHECK FAILED" | tee -a "$O/crosscheck.txt"; exit 1
