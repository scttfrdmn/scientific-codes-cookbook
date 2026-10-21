muscle -align pf01255.25_unaligned.fa -output muscle_aln.fa
n=$(grep -c '^>' muscle_aln.fa)
r=$(awk '!/^>/{gsub(/-/,"");t+=length($0)}END{print t}' muscle_aln.fa)
echo "muscle seqs=$n residues=$r"
[ "$n" -eq 919 ] || { echo "FAIL seqs $n != 919"; exit 1; }
[ "$r" -eq 207712 ] || { echo "FAIL residues $r != 207712"; exit 1; }