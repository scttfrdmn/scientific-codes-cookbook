# MrBayes writes its consensus as NEXUS with a translate block (taxa as integers).
# Convert to plain Newick with real labels, walking the string character by character so
# an integer is only replaced when it is a complete taxon token -- a gsub on digits would
# corrupt branch lengths and support values.
/^[ \t]*translate/ { intrans = 1; next }
intrans {
  line = $0
  gsub(/[,;]/, "", line)
  n = split(line, f, /[ \t]+/)
  # fields come as: <index> <label>
  idx = ""; lab = ""
  for (i = 1; i <= n; i++) if (f[i] != "") { if (idx == "") idx = f[i]; else { lab = f[i]; break } }
  if (idx != "" && lab != "") { map[idx] = lab; ntax++ }
  if ($0 ~ /;[ \t]*$/) intrans = 0
  next
}
/^[ \t]*tree / {
  s = $0
  sub(/^[^=]*=[ \t]*/, "", s)          # drop "tree con_50_majrule = "
  gsub(/\[[^]]*\]/, "", s)             # drop [&U] and any node annotations
  out = ""; num = ""
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c ~ /[0-9]/ && (num != "" || prev ~ /[(,]/)) { num = num c; prev = c; continue }
    if (num != "") { out = out (num in map ? map[num] : num); num = "" }
    out = out c; prev = c
  }
  if (num != "") out = out (num in map ? map[num] : num)
  print out
  printed = 1
}
END {
  if (!printed) { print "ERROR: no tree line found" > "/dev/stderr"; exit 1 }
  if (ntax == 0) { print "ERROR: no translate block" > "/dev/stderr"; exit 1 }
}
