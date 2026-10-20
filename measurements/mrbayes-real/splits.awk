# Non-trivial bipartitions of a Newick tree, one per line: "<bitstring> <label>".
#   awk -v labels=<sorted label file> -f splits.awk tree.nwk
# Each split is a 0/1 string over the label file's order, oriented so the first taxon is 0, so the
# same bipartition prints the same string whichever tree and rooting it came from. <label> is the
# internal-node label (MrBayes' posterior probability on a consensus), "-" if none.
# POSIX awk only (it runs in bioconda's BusyBox images too).
BEGIN {
  n = 0
  while ((getline l < labels) > 0) if (l != "") idx[l] = ++n
  if (n < 4) { print "splits.awk: no labels" > "/dev/stderr"; exit 2 }
}
{ s = s $0 }
END {
  if (n < 4) exit 2
  gsub(/[ \t\r\n]/, "", s)
  L = length(s); i = 1; top = 0; nodes = 0
  while (i <= L) {
    c = substr(s, i, 1)
    if (c == "(") { nodes++; stack[++top] = nodes; mem[nodes] = ""; i++; continue }
    if (c == "," ) { i++; continue }
    if (c == ";" ) break
    if (c == ")") {
      node = stack[top--]; i++
      lab = ""
      while (i <= L && substr(s, i, 1) !~ /[:,();]/) { lab = lab substr(s, i, 1); i++ }
      if (substr(s, i, 1) == ":") { i++; while (i <= L && substr(s, i, 1) !~ /[,();]/) i++ }
      if (top > 0) mem[stack[top]] = mem[stack[top]] " " mem[node]
      emit(mem[node], lab == "" ? "-" : lab)
      continue
    }
    name = ""
    while (i <= L && substr(s, i, 1) !~ /[:,();]/) { name = name substr(s, i, 1); i++ }
    if (substr(s, i, 1) == ":") { i++; while (i <= L && substr(s, i, 1) !~ /[,();]/) i++ }
    if (!(name in idx)) { print "splits.awk: unknown taxon " name > "/dev/stderr"; bad = 1; continue }
    if (top > 0) mem[stack[top]] = mem[stack[top]] " " idx[name]
  }
  exit bad
}
function emit(list, lab,    k, m, a, bits, j, b, one, out) {
  m = split(list, a, " ")
  for (j = 1; j <= n; j++) bits[j] = 0
  k = 0
  for (j = 1; j <= m; j++) if (a[j] != "" && !bits[a[j]]) { bits[a[j]] = 1; k++ }
  if (k < 2 || k > n - 2) return
  one = bits[1]; out = ""
  for (j = 1; j <= n; j++) { b = bits[j]; if (one) b = 1 - b; out = out b }
  print out, lab
}
