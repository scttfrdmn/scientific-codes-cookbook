
iqtree3 -s mafft_aln.fa -m LG+G4 -T 4 -seed 12345 --prefix mafft -quiet
# per-stage identity: a valid ML tree with all taxa and a finite log-likelihood
tips=$(grep -o ',' mafft.treefile | wc -l | tr -d ' '); tips=$((tips+1))
logl=$(awk -F': ' '/Log-likelihood of the tree/{print $2}' mafft.iqtree | awk '{print $1}')
echo "mafft tips=$tips logL=$logl"
[ "$tips" -eq 919 ] || { echo "FAIL tips $tips != 919"; exit 1; }
awk -v l="$logl" 'BEGIN{ if (l+0 < 0 && l != "") exit 0; print "FAIL logL not a finite negative number: " l; exit 1 }'

