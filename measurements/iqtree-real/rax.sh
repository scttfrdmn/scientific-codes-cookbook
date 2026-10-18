#!/usr/bin/env bash
# iqtree-real, task 3 of 3 (RAxML-NG image): check IQ-TREE's LG+G4 optimum on its own ML topology
# with an unrelated code, two ways.
#   point:     RAxML-NG computes the likelihood AT IQ-TREE's optimum (its branch lengths and alpha,
#              nothing optimised) -- the two likelihood functions must agree.
#   optimum:   RAxML-NG optimises branch lengths and alpha STARTING from IQ-TREE's optimum -- it
#              must not find a better point, or IQ-TREE's was not the maximum.
# Plus, reported only: RAxML-NG from task 1's branch lengths, the start IQ-TREE's first pass used.
#
#   bash rax.sh <alignment.fa> <threads>   (reads real.treefile from task 1, evconv.* from task 2)
set +e
set -uo pipefail
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
ALN=$1 T=$2
O=/tmp/out
mkdir -p "$O"
cd /tmp
rx() { p=$1; shift; raxml-ng --evaluate --msa "$ALN" --threads "$T" --seed 12345 --prefix "$p" --redo "$@" > "$O/$p.stdout" 2>&1; echo $?; }
fl() { awk '/^Final LogLikelihood:/{print $3}' "$1.raxml.log"; }
d()  { awk -v a="$1" -v b="$2" 'BEGIN{x=a-b; if(x<0)x=-x; printf "%.6f", x}'; }

T0=$(date +%s)
ALPHA=$(awk '/^Gamma shape alpha:/{print $4}' evconv.iqtree)
RC1=$(rx pt --tree evconv.treefile --model "LG+G4{$ALPHA}" --opt-model off --opt-branches off)
RC2=$(rx op --tree evconv.treefile --model LG+G4 --lh-epsilon 0.001)
RC3=$(rx t1 --tree real.treefile --model LG+G4 --lh-epsilon 0.001)
echo "raxml-ng exits $RC1 $RC2 $RC3 in $(( $(date +%s) - T0 )) s" | tee "$O/times.txt"
for p in pt op t1; do cp "$p.raxml.log" "$O/" 2>/dev/null; done

LI=$(awk '/^Log-likelihood of the tree:/{print $5}' evconv.iqtree)
LP=$(fl pt); LO=$(fl op); L1=$(fl t1)
DP=$(d "$LI" "$LP"); DO=$(d "$LI" "$LO")
GAIN=$(awk -v a="$LO" -v b="$LI" 'BEGIN{printf "%.6f", a-b}')
NT=$(grep -oE '[(,][A-Za-z0-9_]+:' op.raxml.bestTree | wc -l | tr -d ' ')
N=$(grep -c '^>' "$ALN")
{
  echo "raxml_exits                 $RC1 $RC2 $RC3"
  echo "tips                        $NT   want $N"
  echo "iqtree_LG+G4_optimum        $LI   (alpha $ALPHA)"
  echo "raxml_at_that_point         $LP   |diff| $DP   want < 0.01"
  echo "raxml_reoptimised_from_it   $LO   gain $GAIN   want |diff| < 0.01"
  echo "raxml_from_task1_lengths    $L1   (reported: where RAxML-NG's own optimiser stops from that start)"
} | tee "$O/crosscheck.txt"
[ "$RC1$RC2$RC3" = 000 ] && [ "$NT" = "$N" ] && [ -n "$LP" ] && [ -n "$LO" ] && [ -n "$LI" ] \
  && awk -v a="$DP" -v b="$DO" 'BEGIN{exit !(a<0.01 && b<0.01)}' \
  && { echo "CROSSCHECK OK" | tee -a "$O/crosscheck.txt"; exit 0; }
echo "CROSSCHECK FAILED" | tee -a "$O/crosscheck.txt"; exit 1
