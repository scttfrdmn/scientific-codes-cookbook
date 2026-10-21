iqtree3 -rf mafft.treefile muscle.treefile 2>&1 | tee rf.log
rf=$(awk '/Tree0/{print $2; exit}' *.rfdist 2>/dev/null || echo NA)
{
  echo "# nf-spawn Shape-F: mafft vs muscle tree topology (OBSERVATION, not an assertion)"
  echo "robinson_foulds_distance  $rf"
  echo "# RF confounds the alignment-method difference with iqtree's ML-search"
  echo "# stochasticity (measured 26 plain vs 4 UFBoot on the SAME alignments+seed),"
  echo "# so this is reported, not asserted. The recipe's identities are per-stage:"
  echo "# mafft/muscle residue conservation + iqtree valid ML tree with finite logL."
} > rf-observation.txt
cat rf-observation.txt