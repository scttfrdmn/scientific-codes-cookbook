# Three annotators' (record, transcript) protein consequences, against each other and against
# Ensembl's own published peptides.
#   awk -v out=<dir> -f compare.awk pep.tsv snpeff.pairs.tsv csq.pairs.tsv vep.pairs.tsv
# pep.tsv: transcript<TAB>peptide (Ensembl GRCh37.87 pep.all.fa, chr20-22; the stop is not written).
# Prints key<TAB>value. Writes <out>/disagree.tsv (every pair all three annotate but not alike,
# with the reason it was put down to) and <out>/pep_mismatch.tsv (every reported ref residue that
# is not Ensembl's, likewise).
#
# EXACT classes (synonymous, missense, stop_gained) are compared on all four columns; the others
# on class, position and ref, because their alt is not an amino acid in every notation (snpEff
# writes start loss as p.M1?; a stop loss's alt is the first residue of an extension).
#
# A disagreement is put down to one of three transcript properties, each read off Ensembl's
# published peptide rather than off any tool, or else counted as unexplained:
#   x5     the peptide starts with X: the CDS starts mid-codon, Ensembl numbers that partial codon
#          as residue 1, and a tool that does not is one residue behind on the whole transcript
#   codon1 all three name codon 1 with the same residues but disagree whether it is start loss
#   stop   all three read a stop codon inside the CDS and Ensembl's peptide carries one there too
#          (written * , or U for selenocysteine): whether a stop-to-stop change is stop_retained
#          or synonymous is a naming choice
BEGIN { FS = OFS = "\t"; split("snpeff csq vep", T, " "); exact["synonymous"] = exact["missense"] = exact["stop_gained"] = 1 }
FNR == 1 { fi++ }
fi == 1 { pep[$1] = $2; if (substr($2, 1, 1) == "X") x5[$1] = 1; next }
{
  t = T[fi - 1]; k = $1 SUBSEP $2; v = $3 OFS $4 OFS $5 OFS $6
  if ((t, k) in val) { dup[t]++; next }
  val[t, k] = v; inany[k] = inany[k] + (fi == 2 ? 1 : fi == 3 ? 2 : 4); n[t]++
  if ($5 == ".") noaa[t]++
  if ($4 != "." && $5 != ".") {
    if (!($2 in pep)) { nopep[t]++ }
    else {
      s = pep[$2]; res = substr(s, $4, 1)
      ok = ($5 == "*") ? ($4 == length(s) + 1 || res == "*") : (res == $5)
      if (ok) pepok[t]++
      else {
        why = ($5 == "*" && res == "U") ? "sec" : ($2 in x5) ? "x5" : "unexplained"
        pepbad[t, why]++
        print t, $1, $2, $3, $4, $5, res, "len=" length(s), why > (out "/pep_mismatch.tsv")
      }
    }
  }
}
function f(s, i,   a) { split(s, a, OFS); return a[i] }
END {
  for (k in inany) {
    b = inany[k]; nb[b]++
    if (b == 1 && f(val["snpeff", k], 3) == ".") snp_only_noaa++
    if (b != 7) continue
    a = val["snpeff", k]; c = val["csq", k]; e = val["vep", k]
    cl = f(a, 1)
    if (exact[cl] && a == c && a == e) { agree_exact++; agcls[cl]++; continue }
    if (!exact[cl] && cl == f(c, 1) && cl == f(e, 1) && f(a, 2) == f(c, 2) && f(a, 2) == f(e, 2) && f(a, 3) == f(c, 3) && f(a, 3) == f(e, 3)) { agree_other++; agcls[cl]++; continue }
    split(k, K, SUBSEP); tr = K[2]
    sameaa = (f(a, 3) == f(c, 3) && f(a, 3) == f(e, 3) && f(c, 4) == f(e, 4) && (f(a, 4) == f(c, 4) || f(a, 4) == "."))
    if (tr in x5 && f(a, 1) == f(c, 1) && f(a, 1) == f(e, 1) && sameaa && f(a, 2) == f(c, 2) && f(e, 2) == f(a, 2) + 1) why = "x5"
    else if (f(a, 2) == 1 && f(c, 2) == 1 && f(e, 2) == 1 && sameaa) why = "codon1"
    else if (f(a, 3) == "*" && f(c, 3) == "*" && f(e, 3) == "*" && (substr(pep[tr], f(a, 2), 1) == "*" || substr(pep[tr], f(a, 2), 1) == "U")) why = "stop"
    else why = "unexplained"
    nd[why]++; if (why == "x5") x5tx[tr] = 1
    print K[1], tr, a, c, e, why > (out "/disagree.tsv")
  }
  for (i = 1; i <= 3; i++) {
    t = T[i]
    printf "pairs_%s\t%d\n", t, n[t]
    printf "pairs_without_amino_acids_%s\t%d\n", t, noaa[t] + 0
    printf "duplicate_pairs_%s\t%d\n", t, dup[t] + 0
    printf "pep_ref_match_%s\t%d\n", t, pepok[t] + 0
    printf "pep_ref_mismatch_x5_%s\t%d\n", t, pepbad[t, "x5"] + 0
    printf "pep_ref_mismatch_sec_%s\t%d\n", t, pepbad[t, "sec"] + 0
    printf "pep_ref_mismatch_unexplained_%s\t%d\n", t, pepbad[t, "unexplained"] + 0
    printf "pep_transcript_absent_%s\t%d\n", t, nopep[t] + 0
  }
  # membership bits: 1 snpEff, 2 csq, 4 VEP
  split("snpeff_only csq_only snpeff+csq vep_only snpeff+vep csq+vep all_three", L, " ")
  for (i = 1; i <= 7; i++) printf "pairs_in_%s\t%d\n", L[i], nb[i] + 0
  printf "snpeff_only_pairs_without_amino_acids\t%d\n", snp_only_noaa + 0
  printf "all_three_agree_exact_classes\t%d\n", agree_exact
  printf "all_three_agree_other_classes\t%d\n", agree_other
  for (c in agcls) printf "all_three_agree_%s\t%d\n", c, agcls[c]
  printf "all_three_disagree_x5\t%d\n", nd["x5"] + 0
  printf "all_three_disagree_codon1\t%d\n", nd["codon1"] + 0
  printf "all_three_disagree_stop\t%d\n", nd["stop"] + 0
  printf "all_three_disagree_unexplained\t%d\n", nd["unexplained"] + 0
  nx = 0; for (tr in x5tx) nx++
  printf "x5_transcripts_with_offset_pairs\t%d\n", nx
  nx = 0; for (tr in x5) nx++
  printf "x5_transcripts_in_ensembl\t%d\n", nx
}
