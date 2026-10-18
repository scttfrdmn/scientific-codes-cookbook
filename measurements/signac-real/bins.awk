# bins.awk -- per-barcode recount of a fragments file, without Signac.
#   awk -v N=<lines> -v OUT=<dir>/ -f bins.awk cells.txt <fragments on stdin, decompressed>
# Writes OUT barcodes.tsv: barcode, records, sum(col5), <147, 148..293, {147,294}, >=295
#        OUT nfirst.tsv:   cell, <147, 148..293 -- over the first N records only, which is what
#                          NucleosomeSignal reads (n = ncol(object) * 5000, header lines excluded)
# POSIX awk only: it runs under whatever awk the image ships.
BEGIN { FS = OFS = "\t" }
FNR == 1 { fi++ }
fi == 1 { iscell[$1] = 1; next }
/^#/ { next }
{
  r++; L = $3 - $2; cb = $4
  if (!(cb in rec)) nb++
  rec[cb]++; rd[cb] += $5
  if (L < 147) { nf[cb]++; b = 1 }
  else if (L > 147 && L < 294) { mo[cb]++; b = 2 }
  else if (L == 147 || L == 294) { ho[cb]++; b = 3 }
  else { di[cb]++; b = 4 }
  if (L > maxL) maxL = L
  if (r <= N && (cb in iscell)) { if (b == 1) fnf[cb]++; else if (b == 2) fmo[cb]++ }
}
END {
  for (cb in rec)
    print cb, rec[cb], rd[cb], nf[cb] + 0, mo[cb] + 0, ho[cb] + 0, di[cb] + 0 > (OUT "barcodes.tsv")
  for (cb in iscell) print cb, fnf[cb] + 0, fmo[cb] + 0 > (OUT "nfirst.tsv")
  printf "bins\trecords %d  barcodes %d  max_length %d  first_N %d\n", r, nb, maxL, N
}
