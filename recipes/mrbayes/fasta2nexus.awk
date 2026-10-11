# FASTA -> NEXUS for MrBayes. Lossless by construction and label-preserving, which is
# what lets the resulting tree be compared with the ML recipes' trees: the Pfam headers
# in this alignment are [0-9A-Z_] only, with no duplicates, so no quoting or renaming is
# needed. Staging asserts that property rather than assuming it -- a sanitized label
# would turn a topology comparison into a label-matching exercise, silently.
/^>/ { name = substr($0, 2); order[++n] = name; next }
      { seq[name] = seq[name] $0 }
END   {
  if (n == 0) { print "ERROR: no FASTA records" > "/dev/stderr"; exit 1 }
  L = length(seq[order[1]])
  for (i = 1; i <= n; i++)
    if (length(seq[order[i]]) != L) {
      printf "ERROR: %s is %d chars, expected %d\n", order[i], length(seq[order[i]]), L \
        > "/dev/stderr"
      exit 1
    }
  print "#NEXUS"
  print "begin data;"
  printf "  dimensions ntax=%d nchar=%d;\n", n, L
  print "  format datatype=protein gap=- missing=?;"
  print "  matrix"
  for (i = 1; i <= n; i++) printf "    %s %s\n", order[i], seq[order[i]]
  print "  ;"
  print "end;"
}
