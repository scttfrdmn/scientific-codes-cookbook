# One annotated VCF -> one row per (SNV record, transcript) with a protein-level consequence:
#   ID  transcript  class  protein_pos  ref_aa  alt_aa
# so snpEff, bcftools csq and VEP can be compared on the same five columns.
#   awk -v tool=snpeff|csq|vep -v sum=<summary file> -f pairs.awk   (VCF text on stdin)
# Only biallelic SNVs (the input is split, so every record has one ALT): for an SNV inside a
# codon the protein consequence is fixed by the genetic code, so three correct annotators must
# agree on it -- indel conventions (where in a repeat, which codon a frameshift starts at) are
# the documented differences, not errors, and stay out of the assertion.
# The class is the most severe protein-level term in the tool's compound consequence, so
# `splice_region_variant&synonymous_variant` and `synonymous&splice_region` both read synonymous.
# Portable awk (mawk, gawk, BusyBox).
BEGIN { FS = "\t"; OFS = "\t"
  nrank = split("start_lost stop_gained stop_lost stop_retained start_retained missense synonymous", R, " ")
  for (i = 1; i <= nrank; i++) rank[R[i]] = i
}
function cls(terms,   n, t, i, x, best, b) {
  n = split(terms, t, "&"); best = ""; b = 99
  for (i = 1; i <= n; i++) {
    x = t[i]; sub(/^[*@]/, "", x); sub(/_variant$/, "", x)
    if (x == "initiator_codon") x = "start_retained"       # snpEff's name for a start codon that stays ATG-like
    if (x in rank && rank[x] < b) { b = rank[x]; best = x }
  }
  return best
}
function tx(id) { sub(/^transcript:/, "", id); sub(/\.[0-9]+$/, "", id); return id }
function emit(id, t, c, p, r, a) {
  if (c == "") return
  if (p == "" || p !~ /^[0-9]+$/) p = "."
  if (r !~ /^[A-Z*]$/) r = "."
  if (a !~ /^[A-Z*]$/) a = "."
  print id, tx(t), c, p, r, a
  npairs++; ncls[c]++
}
/^##INFO=<ID=CSQ,/ && tool == "vep" {           # VEP's field order is in its own header
  hs = $0; sub(/.*Format: /, "", hs); sub(/">.*/, "", hs)
  n = split(hs, hd, "|"); for (i = 1; i <= n; i++) vc[hd[i]] = i
  next
}
/^#/ { next }
{
  nrec++
  tag = (tool == "snpeff") ? "ANN=" : (tool == "csq") ? "BCSQ=" : "CSQ="
  v = ""; m = split($8, kv, ";")
  for (i = 1; i <= m; i++) if (index(kv[i], tag) == 1) { v = substr(kv[i], length(tag) + 1); break }
  if (v != "") ntag++
  if ($4 !~ /^[ACGT]$/ || $5 !~ /^[ACGT]$/) next
  nsnv++
  if (v == "") next
  k = split(v, e, ",")
  for (j = 1; j <= k; j++) {
    n = split(e[j], f, "|")
    if (tool == "snpeff") {
      # Allele|Annotation|Impact|Gene_Name|Gene_ID|Feature_Type|Feature_ID|Biotype|Rank|HGVS.c|HGVS.p|cDNA|CDS|AA|Distance|Errors
      if (f[6] != "transcript") continue
      c = cls(f[2]); p = f[14]; sub(/\/.*/, "", p)
      hp = f[11]; r = "."; a = "."
      if (hp ~ /^p\.[A-Z*][0-9]+/) {
        r = substr(hp, 3, 1); rest = substr(hp, 4); sub(/^[0-9]+/, "", rest)
        a = substr(rest, 1, 1); if (a == "=") a = r
      }
      emit($3, f[7], c, p, r, a)
    } else if (tool == "csq") {
      # Consequence|gene|transcript|biotype|strand|amino_acid_change|dna_change
      c = cls(f[1]); aa = f[6]; p = "."; r = "."; a = "."
      if (aa ~ /^[0-9]+[A-Z*]/) {
        p = aa; sub(/[^0-9].*/, "", p)
        r = substr(aa, length(p) + 1, 1)
        if (index(aa, ">")) { rest = substr(aa, index(aa, ">") + 1); sub(/^[0-9]+/, "", rest); a = substr(rest, 1, 1) }
        else a = r
      }
      emit($3, f[3], c, p, r, a)
    } else {
      if (vc["Feature_type"] && f[vc["Feature_type"]] != "Transcript") continue
      c = cls(f[vc["Consequence"]]); p = f[vc["Protein_position"]]; aa = f[vc["Amino_acids"]]
      r = "."; a = "."
      if (aa ~ /^[A-Z*]\/[A-Z*]$/) { r = substr(aa, 1, 1); a = substr(aa, 3, 1) }
      else if (aa ~ /^[A-Z*]$/) { r = aa; a = aa }
      emit($3, f[vc["Feature"]], c, p, r, a)
    }
  }
}
END {
  printf "records\t%d\nrecords_annotated\t%d\nsnv_records\t%d\npairs\t%d\n", nrec, ntag, nsnv, npairs > sum
  for (i = 1; i <= nrank; i++) printf "pairs_%s\t%d\n", R[i], ncls[R[i]] + 0 > sum
}
