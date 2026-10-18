#!/usr/bin/env bash
# iqtree-real, task 2 of 3 (IQ-TREE image): re-optimise task 1's ML topology under a fixed LG+G4
# until it stops moving, for task 3's cross-check against RAxML-NG.
#
#   bash eval.sh <alignment.fa> <threads>      (reads real.treefile from task 1)
#
# Why a loop: `-te` starts from the branch lengths in the tree it is given. From task 1's (fitted
# under the chosen FreeRate model) one pass stopped 151 log-likelihood units short of the LG+G4
# optimum, on a tree 44% too long, and a second pass from its own output climbed the rest. So the
# evaluated value is a fixed point only once evaluating it again does not move it; that is asserted.
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
ALN=$1 T=$2
O=/tmp/out
mkdir -p "$O"
cd /tmp
lnl() { awk '/^Log-likelihood of the tree:/{print $5}' "$1.iqtree"; }
ev() { iqtree3 -s "$ALN" -te "$2" -m LG+G4 -T "$T" --seed 12345 -me 0.001 --prefix "$1" -redo > "$O/$1.stdout" 2>&1; }

T0=$(date +%s)
ev ev0 real.treefile
PREV=$(lnl ev0)
echo "pass 0 $PREV  alpha $(awk '/^Gamma shape alpha:/{print $4}' ev0.iqtree)  length $(awk '/^Total tree length/{print $NF}' ev0.iqtree)" | tee "$O/passes.txt"
CONV=0
for i in 1 2 3 4 5; do
  ev "ev$i" "ev$((i-1)).treefile"
  CUR=$(lnl "ev$i")
  echo "pass $i $CUR  alpha $(awk '/^Gamma shape alpha:/{print $4}' "ev$i.iqtree")  length $(awk '/^Total tree length/{print $NF}' "ev$i.iqtree")" | tee -a "$O/passes.txt"
  if awk -v a="$CUR" -v b="$PREV" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(a!="" && d<0.001)}'; then CONV=1; break; fi
  PREV=$CUR
done
echo "eval wall $(( $(date +%s) - T0 )) s" | tee "$O/times.txt"
cp ev0.iqtree ev0.treefile "$O/"
cp "ev$i.iqtree" "$O/evconv.iqtree"; cp "ev$i.treefile" "$O/evconv.treefile"

N=$(grep -c '^>' "$ALN")
NT=$(grep -oE '[(,][A-Za-z0-9_]+:' "ev$i.treefile" | wc -l | tr -d ' ')
M=$(awk -F': *' '/^Model of substitution/{print $2}' "ev$i.iqtree")
{
  echo "converged          $CONV   want 1 (re-evaluating moves logL < 0.001, within 5 passes)"
  echo "passes             $i"
  echo "first_pass_logL    $(lnl ev0)"
  echo "fixed_point_logL   $CUR"
  echo "tips               $NT   want $N"
  echo "model              $M   want LG+G4"
} | tee "$O/eval-check.txt"
[ "$CONV" = 1 ] && [ "$NT" = "$N" ] && [ "$M" = "LG+G4" ] && { echo "EVAL OK" | tee -a "$O/eval-check.txt"; exit 0; }
echo "EVAL FAILED" | tee -a "$O/eval-check.txt"; exit 1
