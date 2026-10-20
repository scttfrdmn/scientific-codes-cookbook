# measurements/kallisto-real task 3 (RSEM image, for its R; Graviton4): every chip's two kallisto
# runs checked at full printed precision, ranked against salmon-real and rsem-real on the same reads
# and the same FASTA, and compared across chips.
#   Rscript check.R <dir with arm64/ x86_64/ amd/, each holding A/ B/ pins-check.txt> <gene_map.tsv>
#                   <salmon quant.sf> <RSEM isoforms.results> <RSEM genes.results>
# The rank floors were set before the run from the sibling pairs: salmon~kallisto 0.908 on these
# reads (quant-depth), RSEM~salmon 0.944 transcripts and 0.981 genes (rsem-real).
a <- commandArgs(trailingOnly = TRUE)
D <- a[1]
CHIPS <- c("arm64", "x86_64", "amd")
rd <- function(f, ...) read.delim(f, quote = "", comment.char = "", stringsAsFactors = FALSE, check.names = FALSE, ...)
map <- read.delim(a[2], header = FALSE, quote = "", comment.char = "", stringsAsFactors = FALSE, col.names = c("gene", "tx"))
sal <- rd(a[3]); rsi <- rd(a[4]); rsg <- rd(a[5])

lines <- character(); bad <- 0
rec <- function(name, value, want = "", ok = NULL) {   # ok NULL = observation; NA fails
  v <- paste(vapply(as.list(value), function(x) if (is.numeric(x)) format(x, digits = 12) else as.character(x), ""),
             collapse = " ")
  st <- if (is.null(ok)) "obs" else if (isTRUE(as.logical(ok))) "PASS" else { bad <<- bad + 1; "FAIL" }
  l <- paste(st, name, v, want, sep = "\t"); lines <<- c(lines, l); cat(l, "\n", sep = "")
}
# Half a unit in the last printed place. abundance.tsv is written with the stream default, 6
# significant digits with trailing zeros dropped ("3" is 3.00000), so a value x is within
# 0.5 x 10^(floor(log10 x) - 5) of what was computed: the exact rounding bound, not a chosen one.
hulp <- function(s) {
  x <- abs(as.numeric(s))
  ifelse(x > 0, 0.5 * 10^(floor(log10(x) + 1e-9) - 5), 0)
}
ab <- function(c, r) {
  f <- file.path(D, c, r, "abundance.tsv")
  s <- rd(f, colClasses = "character")
  data.frame(id = s$target_id, len = as.numeric(s$length), eff = as.numeric(s$eff_length),
             est = as.numeric(s$est_counts), tpm = as.numeric(s$tpm),
             h_eff = hulp(s$eff_length), h_est = hulp(s$est_counts), h_tpm = hulp(s$tpm), stringsAsFactors = FALSE)
}
info <- function(c, r) {
  j <- paste(readLines(file.path(D, c, r, "run_info.json")), collapse = "")
  g <- function(k) as.numeric(sub(paste0('.*"', k, '": *([0-9.]+).*'), "\\1", j))
  c(n_processed = g("n_processed"), n_pseudoaligned = g("n_pseudoaligned"), n_unique = g("n_unique"))
}
gene_of <- function(ids) map$gene[match(ids, map$tx)]
sp <- function(x, y) { b <- x > 0 & y > 0; c(cor(x[b], y[b], method = "spearman"), sum(b)) }

st <- sal$TPM[match(map$tx, sal$Name)]; st[is.na(st)] <- 0          # salmon drops identical sequences: 0
rt <- rsi$TPM[match(map$tx, rsi$transcript_id)]
sg <- tapply(st, map$gene, sum)
rg <- rsg$TPM[match(names(sg), rsg$gene_id)]
rec("salmon ids in the gene map / RSEM ids == gene map ids", c(all(sal$Name %in% map$tx), setequal(rsi$transcript_id, map$tx)),
    "both TRUE (same FASTA)", all(sal$Name %in% map$tx) && setequal(rsi$transcript_id, map$tx))

R <- list()
for (c in CHIPS) for (r in c("A", "B")) {
  k <- ab(c, r); n <- info(c, r); R[[paste(c, r)]] <- list(k = k, n = n)
  p <- paste0(c, " ", r, ": ")
  rec(paste0(p, "ids == gene map transcripts, in FASTA order"), setequal(k$id, map$tx), "set equality", setequal(k$id, map$tx))
  rec(paste0(p, "n_processed n_pseudoaligned n_unique"), n)
  b <- sum(k$h_est[k$est > 0])
  rec(paste0(p, "sum est_counts - n_pseudoaligned"), sum(k$est) - n[["n_pseudoaligned"]],
      sprintf("|.| <= %.4f (half-ulp of every printed est_count)", b), abs(sum(k$est) - n[["n_pseudoaligned"]]) <= b)
  bt <- sum(k$h_tpm[k$tpm > 0])
  rec(paste0(p, "sum tpm - 1e6"), sum(k$tpm) - 1e6, sprintf("|.| <= %.4f", bt), abs(sum(k$tpm) - 1e6) <= bt)
  # tpm_i = est_i / eff_i x 1e6 / sum_j(est_j / eff_j) (kallisto's counts_to_tpm): every row on one
  # line through the origin. Bound per row: the printed rounding of tpm, est and eff, propagated,
  # plus the propagated error of the constant itself.
  e <- k$est > 0
  rr <- k$est[e] / k$eff[e]; drr <- k$h_est[e] / k$eff[e] + k$est[e] * k$h_eff[e] / k$eff[e]^2
  K <- 1e6 / sum(rr); dK <- K * sum(drr) / sum(rr)
  dev <- abs(k$tpm[e] - K * rr); tol <- k$h_tpm[e] + K * drr + dK * rr
  rec(paste0(p, "rows off tpm = K est/eff"), sum(dev > tol), "== 0 (bound: printed roundings + K's)", all(dev <= tol))
  rec(paste0(p, "tpm where est_counts == 0"), sum(k$tpm[!e]), "== 0", all(k$tpm[!e] == 0))
  kt <- k$tpm[match(map$tx, k$id)]
  kg <- tapply(kt, map$gene, sum)
  s1 <- sp(kt, st); s2 <- sp(kt, rt); s3 <- sp(as.numeric(kg), as.numeric(sg)); s4 <- sp(as.numeric(kg), rg)
  rec(paste0(p, "transcript Spearman vs salmon (n)"), s1, ">= 0.85", s1[1] >= 0.85)
  rec(paste0(p, "transcript Spearman vs RSEM (n)"), s2, ">= 0.85", s2[1] >= 0.85)
  rec(paste0(p, "gene Spearman vs salmon (n)"), s3, ">= 0.90", s3[1] >= 0.90)
  rec(paste0(p, "gene Spearman vs RSEM (n)"), s4, ">= 0.90", s4[1] >= 0.90)
  b2 <- kt > 0 & st > 0
  rec(paste0(p, "transcript log(1+TPM) Pearson vs salmon"), cor(log1p(kt[b2]), log1p(st[b2])))
  rec(paste0(p, "detected (tpm > 0)"), sum(k$tpm > 0))
}
for (c in CHIPS) {
  x <- R[[paste(c, "A")]]$k; y <- R[[paste(c, "B")]]$k
  rec(paste0(c, ": A vs B transcript Spearman (either detected)"),
      cor(x$tpm[x$tpm > 0 | y$tpm > 0], y$tpm[x$tpm > 0 | y$tpm > 0], method = "spearman"))
}

# -- across chips ------------------------------------------------------------------------------
idx <- vapply(CHIPS, function(c) { l <- grep("kallisto.idx", readLines(file.path(D, c, "pins-check.txt")), value = TRUE)
  sub(" .*", "", l[length(l)]) }, "")
rec("index sha256 on arm64 / x86_64 / amd", idx, "one file", length(unique(idx)) == 1)
for (b in c("x86_64", "amd")) for (r in c("A", "B")) {
  x <- R[[paste("arm64", r)]]; y <- R[[paste(b, r)]]
  p <- paste0(b, " vs arm64, ", r, ": ")
  rec(paste0(p, "n_processed n_pseudoaligned n_unique identical"), identical(x$n, y$n),
      "pseudoalignment is per read, so thread order cannot move it", identical(x$n, y$n))
  same <- identical(readLines(file.path(D, "arm64", r, "abundance.tsv")), readLines(file.path(D, b, r, "abundance.tsv")))
  # B fixes the fragment-length distribution, the one input thread order can move, so what is left is
  # pseudoalignment (per read) and a serial EM over integer class counts: a fixed point, asserted.
  # A estimates the distribution from whichever pairs the threads reach first: a path, reported.
  if (r == "B") rec(paste0(p, "abundance.tsv byte-identical"), same, "fixed point", same) else
    rec(paste0(p, "abundance.tsv byte-identical"), same)
  m <- pmax(x$k$est, y$k$est)
  rec(paste0(p, "rows differing / max rel diff est_counts (est >= 1) / max |diff| eff_length"),
      c(sum(x$k$est != y$k$est | x$k$eff != y$k$eff), max(abs(x$k$est - y$k$est)[m >= 1] / m[m >= 1]), max(abs(x$k$eff - y$k$eff))))
  e <- x$k$tpm > 0 | y$k$tpm > 0
  rec(paste0(p, "tpm Spearman (either detected)"), cor(x$k$tpm[e], y$k$tpm[e], method = "spearman"))
}
x <- readLines(file.path(D, "x86_64", "B", "abundance.tsv")); y <- readLines(file.path(D, "amd", "B", "abundance.tsv"))
rec("amd vs x86_64, B: abundance.tsv byte-identical", identical(x, y), "fixed point", identical(x, y))
x <- readLines(file.path(D, "x86_64", "A", "abundance.tsv")); y <- readLines(file.path(D, "amd", "A", "abundance.tsv"))
rec("amd vs x86_64, A: abundance.tsv byte-identical", identical(x, y))

writeLines(lines, file.path(D, "check.txt"))
if (bad > 0) { cat(bad, "check(s) failed\n"); quit(status = 1) }
cat("ALL CHECKS PASSED\n")
