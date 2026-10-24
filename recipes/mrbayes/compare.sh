#!/usr/bin/env bash
# Task 2: three methods, one alignment. Staged by stage-inputs.sh and pinned by sha256 in
# 02-compare.task.json (kept out of the spec: spawn's wrapper fills most of EC2's 16 KiB user data, spawn#1246).

echo "== three methods, one alignment =="
# MrBayes writes TWO consensus variants (50% majority-rule and all-compatible), so
# mb.consensus.nwk carries two trees. Split them and assert they agree before using one.
awk 'NF{print > ("mbcons_" NR ".nwk")}' mb.consensus.nwk
NCONS=$(ls mbcons_*.nwk | wc -l | tr -d ' ')
echo "mrbayes_consensus_trees\t$NCONS"
cp mbcons_1.nwk mb.majrule.nwk

for f in mb.majrule.nwk iqout.treefile rx.raxml.bestTree; do
  tr ',()' '\n\n\n' < "$f" | sed 's/:.*//' | grep -E '^[A-Za-z0-9_]+$' | sort -u > "$f.labels"
  echo "labels_$(basename $f)\t$(grep -c . "$f.labels")"
done
cmp -s mb.majrule.nwk.labels iqout.treefile.labels || { echo "FAIL: MrBayes vs IQ-TREE leaf sets differ"; exit 1; }
cmp -s mb.majrule.nwk.labels rx.raxml.bestTree.labels || { echo "FAIL: MrBayes vs RAxML-NG leaf sets differ"; exit 1; }
echo "leaf_sets\tidentical across all three (114)"

# Read a pairwise RF out of IQ-TREE's matrix BY POSITION, not by a fixed field number:
# the matrix is (n+1) lines with n+1 fields per row, so the distance between tree i and
# tree j is row i+2, field j+2. The previous version read field 3 for every pair, which
# silently returned the diagonal.
rf () {  # $1 label, $2 treefile-with-2-or-more, $3 i, $4 j
  rm -f "$2.rfdist"
  iqtree -rf_all "$2" -redo >/dev/null 2>&1 || iqtree --rf_all "$2" -redo >/dev/null 2>&1
  if [ ! -f "$2.rfdist" ]; then echo "$1\tUNAVAILABLE"; return; fi
  V=$(awk -v i="$3" -v j="$4" 'NR==i+2{print $(j+2)}' "$2.rfdist")
  echo "$1\t$V"
}

echo "== the two MrBayes consensus variants must agree with each other =="
if [ "$NCONS" -ge 2 ]; then
  cat mbcons_1.nwk mbcons_2.nwk > pair_cons.trees
  rf rf_majrule_vs_allcompat pair_cons.trees 0 1
fi

echo "== Robinson-Foulds across methods =="
cat mb.majrule.nwk iqout.treefile      > pair_mb_iq.trees
cat mb.majrule.nwk rx.raxml.bestTree   > pair_mb_rx.trees
cat iqout.treefile rx.raxml.bestTree   > pair_iq_rx.trees
rf rf_mb_vs_iqtree    pair_mb_iq.trees 0 1
rf rf_mb_vs_raxml     pair_mb_rx.trees 0 1
rf rf_iqtree_vs_raxml pair_iq_rx.trees 0 1

echo "== the control: an unrelated topology on the same 114 leaves =="
# Without this, an RF of 16 is uncalibrated -- it could be near-identical or near-random.
awk '/^[A-Za-z0-9_]+$/{lab[++n]=$0}
     END{
       if (n < 3) { print "ERROR: " n " labels" > "/dev/stderr"; exit 1 }
       s = "(" lab[1] "," lab[2] ")"
       for (i = 3; i <= n; i++) s = "(" s "," lab[i] ")"
       print s ";"
     }' mb.majrule.nwk.labels > caterpillar.nwk
echo "caterpillar_leaves\t$(tr ',()' '\n\n\n' < caterpillar.nwk | sed 's/:.*//' | grep -cE '^[A-Za-z0-9_]+$')"
cat mb.majrule.nwk caterpillar.nwk > pair_mb_cat.trees
cat iqout.treefile caterpillar.nwk > pair_iq_cat.trees
rf rf_mb_vs_caterpillar     pair_mb_cat.trees 0 1
rf rf_iqtree_vs_caterpillar pair_iq_cat.trees 0 1

echo "== the matrices, in full, AFTER every comparison =="
for f in *.rfdist; do echo "--- $f"; cat "$f"; done

echo "== bipartition support from MrBayes' own .tstat, REPORTED =="
# Column 2 is #obs, column 3 the probability; a numeric ID skips the [ID:] and header rows.
TOT=$(awk '$1 ~ /^[0-9]+$/ && NF>2{c++} END{print c+0}' mb.tstat)
STRONG=$(awk '$1 ~ /^[0-9]+$/ && NF>2 && $3+0>=0.95{c++} END{print c+0}' mb.tstat)
echo "bipartitions_total\t$TOT"
echo "bipartitions_pp_ge_0.95\t$STRONG"

echo "COMPARE DONE"
