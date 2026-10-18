# windows.awk -- GeneActivity's regions from the annotation table, without Signac.
#   awk -f windows.awk genes.tsv > windows.bed      (chrom, start0, end0, index, gene_id)
# Per gene_id (first appearance order): min start, max end, first strand and biotype. Keep
# protein_coding with width (1-based, closed) < 500000, extend 2 kb upstream (strand-aware), and
# write it half-open. This is GeneActivity's documented default (biotypes, max.width,
# extend.upstream = 2000); the task asserts Signac derives the same regions.
BEGIN { FS = OFS = "\t" }
NR == 1 { next }
{
  g = $6
  if (!(g in st)) { ng++; ord[ng] = g; ch[g] = $1; st[g] = $2 + 0; en[g] = $3 + 0; sd[g] = $4; bt[g] = $7; next }
  if ($2 + 0 < st[g]) st[g] = $2 + 0
  if ($3 + 0 > en[g]) en[g] = $3 + 0
}
END {
  for (i = 1; i <= ng; i++) {
    g = ord[i]
    if (bt[g] != "protein_coding" || en[g] - st[g] + 1 >= 500000) continue
    s = st[g]; e = en[g]
    if (sd[g] == "-") e += 2000; else s -= 2000
    print ch[g], s - 1, e, ++k, g
  }
}
