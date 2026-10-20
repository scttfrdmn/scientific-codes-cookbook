# measurements/rsem-real: the checks on one chip's RSEM run, base R only (R ships in the RSEM image
# on both architectures; python does not).
#   Rscript check.R <out dir> <gene_map.tsv> <salmon quant.sf>
# Every bound on a printed value is derived from RSEM's own %.2f rounding (WriteResults.h), not
# chosen to pass: a sum of n printed values can be off by at most 0.005 n.
a <- commandArgs(trailingOnly = TRUE)
O <- a[1]
rd <- function(f) read.delim(f, quote = "", comment.char = "", stringsAsFactors = FALSE, check.names = FALSE)
iso <- rd(file.path(O, "ERR188026.isoforms.results"))
gen <- rd(file.path(O, "ERR188026.genes.results"))
map <- read.delim(a[2], header = FALSE, quote = "", comment.char = "", stringsAsFactors = FALSE,
                  col.names = c("gene", "tx"))
sal <- rd(a[3])
cnt <- scan(file.path(O, "ERR188026.cnt"), what = numeric(), nlines = 1, quiet = TRUE)
names(cnt) <- c("N0", "N1", "N2", "N_tot")
th <- readLines(file.path(O, "ERR188026.theta"))
theta1 <- as.numeric(strsplit(th[2], " ")[[1]])     # theta' -- the last M-step, before polishing

lines <- character(); bad <- 0
rec <- function(name, value, want = "", ok = NA) {
  v <- paste(vapply(as.list(value), function(x) if (is.numeric(x)) format(x, digits = 12) else as.character(x), ""),
             collapse = " ")
  st <- if (is.na(ok)) "obs" else if (isTRUE(as.logical(ok))) "PASS" else { bad <<- bad + 1; "FAIL" }
  l <- paste(st, name, v, want, sep = "\t"); lines <<- c(lines, l); cat(l, "\n", sep = "")
}

# -- read bookkeeping -------------------------------------------------------------------------
rec("N0 N1 N2 N_tot", cnt)
rec("N_tot", cnt[["N_tot"]], "== 15800127 (ENA read_count)", cnt[["N_tot"]] == 15800127)
rec("N0+N1+N2", sum(cnt[1:3]), "== N_tot", sum(cnt[1:3]) == cnt[["N_tot"]])
rec("alignable fraction N1/N_tot", cnt[["N1"]] / cnt[["N_tot"]])

# -- the reference: one row per transcript, named and grouped as the gene map says ------------
rec("isoform rows", nrow(iso), "== transcripts in the map", nrow(iso) == nrow(map))
rec("isoform ids == map ids", setequal(iso$transcript_id, map$tx), "set equality", setequal(iso$transcript_id, map$tx))
g <- map$gene[match(iso$transcript_id, map$tx)]
rec("isoform gene_id == map gene", sum(g != iso$gene_id), "== 0 mismatches", all(g == iso$gene_id))
rec("gene rows", nrow(gen), "== distinct genes in the map", nrow(gen) == length(unique(map$gene)))
rec("effective_length <= length", sum(iso$effective_length > iso$length), "== 0 violations",
    all(iso$effective_length <= iso$length))
z <- iso$effective_length == 0
rec("TPM where effective_length == 0", sum(iso$TPM[z]), "== 0", all(iso$TPM[z] == 0))

# -- conservation -----------------------------------------------------------------------------
nz <- sum(iso$TPM > 0)
rec("isoforms with TPM > 0", nz)
s <- sum(iso$TPM)
rec("sum isoform TPM", s, sprintf("== 1e6 within 0.005 x %d printed nonzero values", nz), abs(s - 1e6) <= 0.005 * nz)
nzg <- sum(gen$TPM > 0)
sg <- sum(gen$TPM)
rec("sum gene TPM", sg, sprintf("== 1e6 within 0.005 x %d", nzg), abs(sg - 1e6) <= 0.005 * nzg)
nzc <- sum(iso$expected_count > 0)
sc <- sum(iso$expected_count)
rec("isoforms with expected_count > 0", nzc)
# Each alignable read's posterior sums to 1 over the transcripts plus RSEM's noise component, so
# sum(expected_count) == N1 - (noise mass). The noise mass is not printed; the last M-step's
# theta'[0] gives it to within one EM step, and it is reported alongside.
noise <- max(theta1[1] * (cnt[["N0"]] + cnt[["N1"]]) - cnt[["N0"]], 0)
rec("theta'[0] x (N0+N1) - N0", noise, "obs: alignable reads' posterior mass on the noise component")
rec("sum expected_count", sc, sprintf("== N1 - noise within 0.005 x %d printed nonzero values", nzc),
    abs(sc - (cnt[["N1"]] - noise)) <= 0.005 * nzc)
rec("N1 - sum expected_count", cnt[["N1"]] - sc, "obs")
rec("expected_count >= 0", sum(iso$expected_count < 0), "== 0 negatives", all(iso$expected_count >= 0))

# TPM is FPKM rescaled by one constant (WriteResults.h: tpm = fpkm / sum(fpkm) * 1e6), so every
# transcript sits on one line. k is estimated from the sums; the per-row bound is the rounding of
# both printed values plus k's own rounding error, propagated.
k <- s / sum(iso$FPKM)
ek <- k * (0.005 * nz / s + 0.005 * sum(iso$FPKM > 0) / sum(iso$FPKM))
dev <- abs(iso$TPM - k * iso$FPKM)
tol <- 0.005 + k * 0.005 + ek * iso$FPKM
rec("TPM / FPKM constant k", k)
rec("rows off the TPM = k FPKM line", sum(dev > tol), "== 0 (bound: both roundings + k's)", all(dev <= tol))

# -- aggregation: a gene is exactly the sum of its isoforms ------------------------------------
agg <- function(v) tapply(v, iso$gene_id, sum)
n_iso <- tapply(rep(1, nrow(iso)), iso$gene_id, sum)
gi <- match(gen$gene_id, names(n_iso))
for (col in c("expected_count", "TPM", "FPKM")) {
  d <- abs(gen[[col]] - agg(iso[[col]])[gi])
  t <- 0.005 * (n_iso[gi] + 1)
  rec(paste("genes where", col, "!= sum of isoforms"), sum(d > t), "== 0 (bound 0.005 x (isoforms + 1))", all(d <= t))
}
pct <- agg(iso$IsoPct)[gi]
e <- gen$TPM > 0
dp <- abs(pct[e] - 100)
rec("expressed genes whose IsoPct does not sum to 100", sum(dp > 0.005 * n_iso[gi][e] + 0.01),
    "== 0 (bound 0.005 x isoforms + 0.01)", all(dp <= 0.005 * n_iso[gi][e] + 0.01))

# -- cross-code: salmon-real's quant of the same reads, against the same FASTA -----------------
# salmon indexes with --keepDuplicates off, so it drops sequence-identical transcripts that RSEM
# keeps (and splits reads between). Its rows are a subset; a dropped duplicate counts as 0 for it.
rec("salmon rows / RSEM rows", c(nrow(sal), nrow(iso)), "obs: salmon collapses identical sequences")
rec("salmon ids not in RSEM", sum(!(sal$Name %in% iso$transcript_id)), "== 0 (same FASTA)",
    all(sal$Name %in% iso$transcript_id))
m <- match(iso$transcript_id, sal$Name)
st <- ifelse(is.na(m), 0, sal$TPM[m])
both <- iso$TPM > 0 & st > 0
rec("transcripts detected by RSEM / salmon / both", c(sum(iso$TPM > 0), sum(st > 0), sum(both)))
rho <- cor(iso$TPM[both], st[both], method = "spearman")
rec("transcript Spearman (both detected)", rho, ">= 0.85 (salmon~kallisto, same reads: 0.908; quant-depth)", rho >= 0.85)
gs <- tapply(st, iso$gene_id, sum)[gi]
gb <- gen$TPM > 0 & gs > 0
rg <- cor(gen$TPM[gb], gs[gb], method = "spearman")
rec("genes detected by both", sum(gb))
rec("gene Spearman (both detected)", rg, ">= 0.90 (isoform ambiguity summed out)", rg >= 0.90)
rec("transcript log-Pearson (both detected)", cor(log(iso$TPM[both]), log(st[both])))
rec("salmon sum NumReads / RSEM sum expected_count", c(sum(sal$NumReads), sc))

writeLines(lines, file.path(O, "check.txt"))
if (bad > 0) { cat(bad, "check(s) failed\n"); quit(status = 1) }
cat("ALL CHECKS PASSED\n")
