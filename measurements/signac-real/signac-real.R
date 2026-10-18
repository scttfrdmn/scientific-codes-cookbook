## Signac on a whole 10x scATAC run (10k PBMCs, Cell Ranger ATAC 2.1.0): the vignette's
## workflow, every count compared with the awk recount (reference.sh), never with Signac itself.
suppressPackageStartupMessages({
  library(Signac); library(SeuratObject); library(GenomicRanges); library(Matrix)
})
## rec(): every value goes through as.character, so a reporting line cannot fail the run.
rec <- function(key, ...) {
  v <- vapply(list(...), function(x) paste(as.character(x), collapse = ","), "")
  cat(key, "\t", paste(v, collapse = "  "), "\n", sep = "")
}
OK <- TRUE
## isTRUE(): an NA or NaN comparison is a failure, never a pass.
check <- function(cond, msg) if (!isTRUE(cond)) { cat("FAIL: ", msg, "\n", sep = ""); OK <<- FALSE }
ver <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) "unknown")
rec("version", "Signac", ver("Signac"), "SeuratObject", ver("SeuratObject"),
    "Rsamtools", ver("Rsamtools"), "RSpectra", ver("RSpectra"), "Matrix", ver("Matrix"),
    "R", paste(R.version$major, R.version$minor, sep = "."))
rec("arch", R.version$arch)
rec("blas", tryCatch(La_library(), error = function(e) "unknown"))
T <- list(); t0 <- proc.time()[["elapsed"]]
lap <- function(name) { t1 <- proc.time()[["elapsed"]]; T[[name]] <<- t1 - t0; rec("time", name, round(t1 - t0, 1), "s"); t0 <<- t1 }
rd <- function(f, ...) read.table(f, sep = "\t", quote = "", comment.char = "", stringsAsFactors = FALSE, ...)
P <- 1000000007
## The recount's checksum, in R: sum(x * (w_i * v_j mod P)) mod P, accumulated in chunks so every
## partial sum stays an exact double (< 2^53).
cksum <- function(m, wi, vj) {
  s <- summary(as(m, "TsparseMatrix"))
  term <- (s$x * ((wi[s$i] * vj[s$j]) %% P)) %% P
  acc <- 0
  for (b in split(seq_along(term), ceiling(seq_along(term) / 1e6))) acc <- (acc + sum(term[b]) %% P) %% P
  c(nnz = nrow(s), sum = sum(s$x), sumsq = sum(s$x^2), ck = acc)
}
dump_md5 <- function(m, f) {          # a canonical byte image of a sparse matrix, for cross-chip md5
  m <- as(m, "CsparseMatrix"); con <- file(f, "wb")
  writeBin(as.integer(dim(m)), con); writeBin(m@i, con); writeBin(m@p, con); writeBin(as.integer(m@x), con)
  close(con); h <- unname(tools::md5sum(f)); unlink(f); h
}

cells <- readLines("ref/cells.txt")
vj <- (seq_along(cells) * 7919) %% 999979 + 1
names(vj) <- cells
st <- rd("ref/stats.tsv", col.names = c("t", "nnz", "sum", "sumsq", "ck"), colClasses = "numeric")
rec("dataset", length(cells), "cells (Cell Ranger is__cell_barcode)")

## ---------------------------------------------------------------- 1. CountFragments, whole file
cf <- CountFragments("fragments.tsv.gz", verbose = FALSE)
lap("CountFragments")
ref <- rd("ref/barcodes.tsv", col.names = c("CB", "rec", "reads", "nfr", "mono", "hole", "di"))
rownames(ref) <- ref$CB
check(nrow(cf) == nrow(ref) && setequal(cf$CB, ref$CB), "CountFragments barcodes != the recount's")
r2 <- ref[cf$CB, ]
check(all(cf$frequency_count == r2$rec), "frequency_count != fragment records per barcode")
check(all(cf$reads_count == r2$reads), "reads_count != sum of column 5 per barcode")
check(all(cf$nucleosome_free == r2$nfr), "nucleosome_free != records < 147 bp")
check(all(cf$mononucleosomal == r2$mono), "mononucleosomal != records of 148..293 bp")
check(all(cf$frequency_count == cf$nucleosome_free + cf$mononucleosomal + r2$di + r2$hole),
      "records are not conserved across the length bins")
rec("count_fragments", "barcodes", nrow(cf), "records", sum(cf$frequency_count), "reads", sum(cf$reads_count),
    "nfr", sum(cf$nucleosome_free), "mono", sum(cf$mononucleosomal), "di", sum(r2$di),
    "len147or294_in_neither_bin", sum(r2$hole),
    "barcodes_differing", sum(cf$frequency_count != r2$rec | cf$reads_count != r2$reads |
                              cf$nucleosome_free != r2$nfr | cf$mononucleosomal != r2$mono))
h_cf <- unname(tools::md5sum({ o <- cf[order(cf$CB), ]; write.table(o, "cf.tsv", sep = "\t", quote = FALSE, row.names = FALSE); "cf.tsv" }))

## ---------------------------------------------------------------- 2. FeatureMatrix over Cell Ranger's peaks
pk <- read.table("peaks.bed", sep = "\t", comment.char = "#", col.names = c("chr", "s0", "e0"), colClasses = c("character", "integer", "integer"))
peaks <- GRanges(pk$chr, IRanges(pk$s0 + 1L, pk$e0))
pnames <- paste(pk$chr, pk$s0 + 1L, pk$e0, sep = "-")
frag <- CreateFragmentObject("fragments.tsv.gz", cells = setNames(cells, cells), verbose = FALSE)
lap("CreateFragmentObject")
fm <- FeatureMatrix(frag, features = peaks, cells = cells, verbose = FALSE)
lap("FeatureMatrix")
pidx <- match(rownames(fm), pnames)
check(!anyNA(pidx), "FeatureMatrix returned a row that is not a peak")
check(setequal(colnames(fm), cells), "FeatureMatrix columns != the cells")
fm <- fm[order(pidx), cells]; pidx <- sort(pidx)
w1 <- (pidx * 40503) %% 999983 + 1
c1 <- cksum(fm, w1, vj)
s1 <- st[st$t == 1, ]
check(c1[["nnz"]] == s1$nnz && c1[["sum"]] == s1$sum && c1[["sumsq"]] == s1$sumsq && c1[["ck"]] == s1$ck,
      "FeatureMatrix != the half-open overlap recount (nnz / sum / sum of squares / checksum)")
rows1 <- rd("ref/rows_1.tsv", col.names = c("idx", "n")); cols1 <- rd("ref/cols_1.tsv", col.names = c("cb", "n"))
rs <- numeric(nrow(pk)); rs[pidx] <- rowSums(fm)
check(all(rs[rows1$idx] == rows1$n), "FeatureMatrix row sums != the recount's per-peak totals")
check(all(colSums(fm)[cols1$cb] == cols1$n), "FeatureMatrix column sums != the recount's per-cell totals")
rec("feature_matrix", "peaks", nrow(pk), "rows_returned", nrow(fm), "cells", ncol(fm), "nnz", c1[["nnz"]],
    "total", c1[["sum"]], "checksum", c1[["ck"]], "recount_checksum", s1$ck,
    "peaks_differing", sum(rs[rows1$idx] != rows1$n))
h_fm <- dump_md5(fm, "fm.bin")

## ---------------------------------------------------------------- 3. object, FRiP, nucleosome signal
gn <- rd("ref/genes.tsv", header = TRUE, colClasses = c("character", "integer", "integer", rep("character", 6)))
anno <- GRanges(gn$seqnames, IRanges(gn$start, gn$end), strand = gn$strand, gene_name = gn$gene_name,
                gene_id = gn$gene_id, gene_biotype = gn$gene_biotype, tx_id = gn$tx_id, type = gn$type)
ca  <- CreateChromatinAssay(counts = fm, sep = c("-", "-"), fragments = frag, annotation = anno, verbose = FALSE)
obj <- CreateSeuratObject(counts = ca, assay = "peaks")
check(ncol(obj) == length(cells) && all(colnames(obj) == cells), "the object does not hold every cell, in order")
obj$total <- cf$frequency_count[match(colnames(obj), cf$CB)]
obj <- FRiP(obj, assay = "peaks", total.fragments = "total", verbose = FALSE)
check(identical(unname(obj$FRiP), cols1$n[match(cells, cols1$cb)] / ref[cells, "rec"]),
      "FRiP != recount peak fragments / recount records")
lap("ChromatinAssay+FRiP")
obj <- suppressWarnings(NucleosomeSignal(obj, verbose = FALSE))
lap("NucleosomeSignal")
nf <- rd("ref/nfirst.tsv", col.names = c("cb", "nfr", "mono")); rownames(nf) <- nf$cb
check(identical(unname(obj$nucleosome_signal), nf[cells, "mono"] / nf[cells, "nfr"]),
      "nucleosome_signal != mono / nfr over the first ncells * 5000 records")
rec("frip", "median", signif(median(obj$FRiP), 6), "range", signif(range(obj$FRiP), 6))
rec("nucleosome_signal", "median", signif(median(obj$nucleosome_signal), 6),
    "nonfinite", sum(!is.finite(obj$nucleosome_signal)), "first_n_records", ncol(obj) * 5000)

## ---------------------------------------------------------------- 4. TSS enrichment (reported)
obj <- suppressWarnings(TSSEnrichment(obj, fast = TRUE, verbose = FALSE))
lap("TSSEnrichment")
tss <- obj$TSS.enrichment
check(all(is.finite(tss)) && all(tss >= 0), "TSS enrichment is not a finite non-negative score for every cell")
rec("tss_enrichment", "median", signif(median(tss), 6), "range", signif(range(tss), 6),
    "cells_below_2", sum(tss < 2), "tss_positions", length(GetTSSPositions(anno)))

## ---------------------------------------------------------------- 5. GeneActivity
ga <- GeneActivity(obj, verbose = FALSE)
lap("GeneActivity")
## Signac's own regions (its collapse, filter and Extend), to map row names back to gene_id and to
## assert they are the regions the recount used.
an2 <- anno; an2$gene_name <- ifelse(is.na(an2$gene_name) | an2$gene_name == "", an2$gene_id, an2$gene_name)
tx <- Signac:::CollapseToLongestTranscript(an2)
tx <- tx[tx$gene_biotype == "protein_coding"]; tx <- tx[width(tx) < 500000]
tx <- Extend(tx, upstream = 2000, downstream = 0)
win <- rd("ref/windows.bed", col.names = c("chr", "s0", "e0", "idx", "gene_id"))
mw <- match(tx$gene_id, win$gene_id)
check(length(tx) == nrow(win) && !anyNA(mw) && all(as.character(seqnames(tx)) == win$chr[mw]) &&
      all(start(tx) - 1L == win$s0[mw]) && all(end(tx) == win$e0[mw]),
      "Signac's GeneActivity regions != windows.awk's")
gidx <- win$idx[mw][match(rownames(ga), tx$gene_name)]
check(!anyNA(gidx), "a GeneActivity row does not map to a gene window")
ga <- ga[order(gidx), cells]; gidx <- sort(gidx)
c2 <- cksum(ga, (gidx * 40503) %% 999983 + 1, vj)
s2 <- st[st$t == 2, ]
check(c2[["nnz"]] == s2$nnz && c2[["sum"]] == s2$sum && c2[["sumsq"]] == s2$sumsq && c2[["ck"]] == s2$ck,
      "GeneActivity != the gene-body + 2 kb overlap recount (nnz / sum / sum of squares / checksum)")
rows2 <- rd("ref/rows_2.tsv", col.names = c("idx", "n"))
gs <- numeric(nrow(win)); gs[gidx] <- rowSums(ga)
check(all(gs[rows2$idx] == rows2$n), "GeneActivity row sums != the recount's per-gene totals")
rec("gene_activity", "windows", nrow(win), "rows", nrow(ga), "nnz", c2[["nnz"]], "total", c2[["sum"]],
    "checksum", c2[["ck"]], "recount_checksum", s2$ck, "genes_differing", sum(gs[rows2$idx] != rows2$n))
h_ga <- dump_md5(ga, "ga.bin")

## ---------------------------------------------------------------- 6. TF-IDF, LSI
obj <- RunTFIDF(obj, method = 1, verbose = FALSE)
X <- LayerData(obj, assay = "peaks", layer = "counts")
D <- LayerData(obj, assay = "peaks", layer = "data")
Xs <- summary(as(X, "TsparseMatrix")); Ds <- summary(as(D, "TsparseMatrix"))
check(nrow(Xs) == nrow(Ds) && all(Xs$i == Ds$i) && all(Xs$j == Ds$j), "TF-IDF changed the sparsity pattern")
closed <- log1p(Xs$x / colSums(X)[Xs$j] * (ncol(X) / rowSums(X)[Xs$i]) * 1e4)
tf_err <- max(abs(Ds$x - closed))
check(tf_err <= 1e-12, "RunTFIDF(method=1) != log1p(tf * idf * 1e4) on a nonzero")
rec("tfidf", "nonzeros", nrow(Ds), "max_abs_diff", signif(tf_err, 3))
rm(Xs, Ds, closed); invisible(gc())
lap("RunTFIDF")
obj <- FindTopFeatures(obj, min.cutoff = "q0", verbose = FALSE)
set.seed(20261016)
obj <- RunSVD(obj, verbose = FALSE)
lap("RunSVD")
m <- Misc(obj[["lsi"]]); k <- length(m$d)
A <- t(D[VariableFeatures(obj), , drop = FALSE])
r1 <- max(sapply(seq_len(k), function(i) sqrt(sum((A %*% m$v[, i] - m$d[i] * m$u[, i])^2)) / m$d[i]))
r2 <- max(sapply(seq_len(k), function(i) sqrt(sum((crossprod(A, m$u[, i]) - m$d[i] * m$v[, i])^2)) / m$d[i]))
ou <- max(abs(crossprod(m$u) - diag(k))); ov <- max(abs(crossprod(m$v) - diag(k)))
check(r1 <= 1e-5 && r2 <= 1e-5, "RunSVD's (u, d, v) are not singular triplets within Signac's tol (1e-5)")
check(ou <= 1e-8 && ov <= 1e-8, "RunSVD's u or v is not orthonormal")
check(all(diff(m$d) <= 0), "RunSVD's singular values are not in decreasing order")
## Deflation: subspace iteration on A - U D V' estimates the largest singular value svds did not
## return. If svds had skipped a component, this would exceed d[k]. Reported, not asserted.
set.seed(1); Q <- qr.Q(qr(matrix(rnorm(ncol(A) * 8), ncol(A))))
for (it in 1:30) {
  Y <- A %*% Q - m$u %*% (m$d * crossprod(m$v, Q))
  Z <- crossprod(A, Y) - m$v %*% (m$d * crossprod(m$u, Y))
  Q <- qr.Q(qr(as.matrix(Z)))
}
Y <- as.matrix(A %*% Q - m$u %*% (m$d * crossprod(m$v, Q)))
next_sv <- max(svd(Y, nu = 0, nv = 0)$d)
lap("SVD checks")
dc <- cor(Embeddings(obj[["lsi"]])[, 1], obj$nCount_peaks)
rec("lsi", "features", length(VariableFeatures(obj)), "components", k)
rec("lsi", "singular_values", signif(m$d, 10))
rec("lsi", "residual_Av", signif(r1, 3), "residual_Atu", signif(r2, 3), "orth_u", signif(ou, 3),
    "orth_v", signif(ov, 3), "d_k", signif(m$d[k], 8), "deflated_top_estimate", signif(next_sv, 8),
    "cor_LSI1_depth", signif(dc, 4))
check(all(is.finite(c(r1, r2, next_sv, dc))), "an LSI diagnostic is not finite")

## ---------------------------------------------------------------- outputs
u <- m$u; sg <- sign(colSums(u)); u <- sweep(u, 2, sg, "*")   # signs are arbitrary: fix them
gz <- gzfile("lsi_u.tsv.gz", "w"); write.table(data.frame(barcode = cells, signif(u, 10)), gz, sep = "\t", quote = FALSE, row.names = FALSE); close(gz)
write.table(data.frame(barcode = colnames(obj), total = obj$total, nCount_peaks = obj$nCount_peaks,
                       FRiP = obj$FRiP, nucleosome_signal = obj$nucleosome_signal, TSS = obj$TSS.enrichment),
            "qc.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
writeLines(sprintf("%.17g", m$d), "singular_values.txt")
h_qc <- unname(tools::md5sum("qc.tsv"))
rec("md5", "count_fragments", h_cf, "feature_matrix", h_fm, "gene_activity", h_ga, "qc", h_qc,
    "singular_values", unname(tools::md5sum("singular_values.txt")))
unlink("cf.tsv")
hwm <- tryCatch(grep("^VmHWM", readLines("/proc/self/status"), value = TRUE), error = function(e) "unknown")
rec("peak_rss", hwm)
rec("time", "signac_total", round(sum(unlist(T)), 1), "s")
if (!OK) quit(status = 1)
cat("SIGNAC OK\n")
