# gtf.awk -- an Ensembl GTF to the annotation table Signac takes as a GRanges.
#   gzip -dc Homo_sapiens.GRCh38.98.gtf.gz | awk -f gtf.awk > genes.tsv
# Exon rows on the standard chromosomes, UCSC-style names (MT -> chrM) to match Cell Ranger's
# GRCh38, and GetGRangesFromEnsDb's default biotypes. Columns are those GetGRangesFromEnsDb gives
# (gene_name, gene_id, gene_biotype, tx_id, type), so no EnsDb or biovizBase is needed.
function attr(s, key,    re) {
  re = key " \"[^\"]*\""
  if (match(s, re)) return substr(s, RSTART + length(key) + 2, RLENGTH - length(key) - 3)
  return ""
}
BEGIN {
  FS = OFS = "\t"
  for (i = 1; i <= 22; i++) std[i] = 1
  std["X"] = 1; std["Y"] = 1; std["MT"] = 1
  keep["protein_coding"] = 1; keep["lincRNA"] = 1; keep["lncRNA"] = 1
  keep["rRNA"] = 1; keep["processed_transcript"] = 1
  print "seqnames", "start", "end", "strand", "gene_name", "gene_id", "gene_biotype", "tx_id", "type"
}
/^#/ { next }
$3 == "exon" && ($1 in std) {
  bt = attr($9, "gene_biotype")
  if (!(bt in keep)) next
  print ($1 == "MT" ? "chrM" : "chr" $1), $4, $5, $7, attr($9, "gene_name"), attr($9, "gene_id"), bt,
        attr($9, "transcript_id"), "exon"
}
