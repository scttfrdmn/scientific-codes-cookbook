# overlap.awk -- recount fragment x interval overlaps, without Signac or GenomicRanges.
#   awk -v OUT=<dir>/ -f overlap.awk cells.txt peaks.sorted.bed windows.sorted.bed <fragments on stdin>
# Interval files: chrom, start0, end0, index -- sorted by chrom then start0. Type 1 = peaks
# (every barcode, for 10x's own per-barcode count), type 2 = gene windows (cells only).
# A fragment [s, e) counts once for an interval [S, E) it overlaps: S < e && E > s, whatever its
# duplicate count -- the semantics the fixture proved Signac's FeatureMatrix uses, edges included.
#
# The fragments file is sorted by start within each chrom, so one sweep suffices: an interval
# joins the active set once S < (some fragment's) e, and leaves -- and is flushed -- once E <= s,
# because no later fragment can reach it. Every active interval is tested exactly.
#
# Per type it writes OUT rows_<t>.tsv (index, total), OUT cols_<t>.tsv (cell, total) and one line
# of OUT stats.tsv: nnz, sum, sum of squares, and a checksum sum(x * (w_i * v_j mod P)) mod P over
# the cell x interval matrix, with fixed weights of the interval index and the cell's position in
# cells.txt -- the same arithmetic in R makes "the same matrix" an exact, cheap comparison.
# It also writes OUT peakfrag.tsv: per barcode, fragments overlapping at least one peak.
BEGIN { FS = OFS = "\t"; P = 1000000007 }
FNR == 1 { fi++ }
fi == 1 { ncell++; cell[$1] = ncell; next }
fi == 2 || fi == 3 {
  t = fi - 1; c = $1; k = ++n[t, c]
  S[t, c, k] = $2 + 0; E[t, c, k] = $3 + 0; ID[t, c, k] = $4
  nid[t]++; ids[t, nid[t]] = $4
  next
}
/^#/ { next }
{
  if ($1 != cur) { flush_all(); cur = $1; p[1] = 1; p[2] = 1 }
  s = $2 + 0; e = $3 + 0; cb = $4
  j = (cb in cell) ? cell[cb] : 0
  if (sweep(1, s, e, j, act1)) pf[cb]++
  if (j) sweep(2, s, e, j, act2)
}
function sweep(t, s, e, j, act,    k, hit, nev, ev, i) {
  while (p[t] <= n[t, cur] && S[t, cur, p[t]] < e) { act[p[t]] = 1; p[t]++ }
  hit = 0; nev = 0
  for (k in act) {
    if (E[t, cur, k] <= s) { ev[++nev] = k; continue }
    if (S[t, cur, k] < e) {
      hit = 1
      if (j) {
        if (!((t, k, j) in cnt)) cl[t, k] = cl[t, k] " " j
        cnt[t, k, j]++
      }
    }
  }
  for (i = 1; i <= nev; i++) { flush(t, ev[i]); delete act[ev[i]] }
  return hit
}
function flush(t, k,    m, a, i, jj, x, id) {
  id = ID[t, cur, k]
  if ((t, k) in cl) {
    m = split(cl[t, k], a, " ")
    for (i = 1; i <= m; i++) {
      jj = a[i]; x = cnt[t, k, jj]; delete cnt[t, k, jj]
      nnz[t]++; tot[t] += x; sq[t] += x * x
      ck[t] = (ck[t] + x * (((id * 40503 % 999983 + 1) * (jj * 7919 % 999979 + 1)) % P)) % P
      row[t, id] += x; col[t, jj] += x
    }
    delete cl[t, k]
  }
}
function flush_all(   k) {
  if (cur == "") return
  for (k in act1) flush(1, k)
  for (k in act2) flush(2, k)
  for (k in act1) delete act1[k]
  for (k in act2) delete act2[k]
}
END {
  flush_all()
  for (t = 1; t <= 2; t++) {
    for (i = 1; i <= nid[t]; i++) printf "%s\t%.0f\n", ids[t, i], row[t, ids[t, i]] + 0 > (OUT "rows_" t ".tsv")
    for (cb in cell) printf "%s\t%.0f\n", cb, col[t, cell[cb]] + 0 > (OUT "cols_" t ".tsv")
    printf "%d\t%.0f\t%.0f\t%.0f\t%.0f\n", t, nnz[t], tot[t], sq[t], ck[t] > (OUT "stats.tsv")
  }
  for (cb in pf) printf "%s\t%.0f\n", cb, pf[cb] > (OUT "peakfrag.tsv")
  printf "overlap\tpeaks: nnz %.0f sum %.0f   windows: nnz %.0f sum %.0f\n", nnz[1], tot[1], nnz[2], tot[2]
}
