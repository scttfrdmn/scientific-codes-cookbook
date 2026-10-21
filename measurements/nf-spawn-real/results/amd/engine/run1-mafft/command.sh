
mafft --retree 2 --maxiterate 0 --thread 1 pf01255.25_unaligned.fa > mafft_aln.fa
# per-stage identity: an aligner must preserve every residue (conservation law)
n=$(grep -c '^>' mafft_aln.fa)
r=$(awk '!/^>/{gsub(/-/,"");t+=length($0)}END{print t}' mafft_aln.fa)
echo "mafft seqs=$n residues=$r"
[ "$n" -eq 919 ] || { echo "FAIL seqs $n != 919"; exit 1; }
[ "$r" -eq 207712 ] || { echo "FAIL residues $r != 207712"; exit 1; }

