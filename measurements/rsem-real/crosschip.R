# measurements/rsem-real task 3: every chip's RSEM output against Graviton4's, and Intel against
# AMD. The read bookkeeping (.cnt) comes from parsing alone, so it must be identical; the EM's
# printed values are a path to a 1e-3 relative stopping rule and are reported, not asserted.
#   Rscript crosschip.R <dir with arm64/ x86_64/ amd/>
D <- commandArgs(trailingOnly = TRUE)[1]
rd <- function(c, f) read.delim(file.path(D, c, paste0("ERR188026.", f)), quote = "", comment.char = "",
                                stringsAsFactors = FALSE, check.names = FALSE)
txt <- function(c, f) readLines(file.path(D, c, paste0("ERR188026.", f)))
out <- character(); bad <- 0
say <- function(...) { l <- paste0(...); out <<- c(out, l); cat(l, "\n", sep = "") }
pairs <- list(c("arm64", "x86_64"), c("arm64", "amd"), c("x86_64", "amd"))
for (p in pairs) {
  a <- p[1]; b <- p[2]
  say("== ", b, " vs ", a, " ==")
  cs <- identical(txt(a, "cnt"), txt(b, "cnt"))
  if (!cs) bad <- bad + 1
  say(if (cs) "PASS" else "FAIL", "\t.cnt (read bookkeeping) identical\t", cs)
  for (f in c("isoforms.results", "genes.results", "theta")) say("obs\t", f, " byte-identical\t", identical(txt(a, f), txt(b, f)))
  # .theta carries the EM's parameters at %.15g: line 2 the last M-step, line 3 after polishing
  for (ln in 2:3) {
    x <- as.numeric(strsplit(txt(a, "theta")[ln], " ")[[1]]); y <- as.numeric(strsplit(txt(b, "theta")[ln], " ")[[1]])
    r <- ifelse(pmax(x, y) > 0, abs(x - y) / pmax(x, y), 0)
    say("obs\ttheta line ", ln, ": components differing / of / max rel diff (components >= 1e-7)\t",
        sum(x != y), " / ", length(x), " / ", format(max(r[pmax(x, y) >= 1e-7]), digits = 4))
  }
  for (f in c("isoforms.results", "genes.results")) {
    x <- rd(a, f); y <- rd(b, f)
    id <- names(x)[1]
    same <- identical(x[[id]], y[[id]])
    if (!same) bad <- bad + 1
    say(if (same) "PASS" else "FAIL", "\t", f, " same ids in the same order\t", same)
    for (col in c("expected_count", "TPM", "effective_length")) {
      d <- abs(x[[col]] - y[[col]])
      say("obs\t", f, " ", col, ": rows differing / max |diff| / sum |diff|\t",
          sum(d > 0), " / ", format(max(d), digits = 6), " / ", format(sum(d), digits = 8))
    }
    e <- x$TPM > 0 | y$TPM > 0
    say("obs\t", f, " TPM Spearman (either detected)\t", format(cor(x$TPM[e], y$TPM[e], method = "spearman"), digits = 10))
    say("obs\t", f, " detected (TPM > 0)\t", sum(x$TPM > 0), " vs ", sum(y$TPM > 0))
  }
}
writeLines(out, file.path(D, "crosschip.txt"))
if (bad > 0) { cat(bad, "check(s) failed\n"); quit(status = 1) }
cat("CROSSCHIP DONE\n")
