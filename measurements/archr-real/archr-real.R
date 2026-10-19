## ArchR on a whole 10x scATAC run (10k PBMCs, Cell Ranger ATAC 2.1.0) -- signac-real's bytes.
## The vignette's path: Arrow file (QC, TileMatrix, GeneScoreMatrix), Cell Ranger's peaks as a
## PeakMatrix, iterative LSI, Seurat clusters and marker genes. Every count is compared with
## recount/ (Go, no ArchR), never with ArchR itself; LSI and clusters are paths, so reported.
suppressPackageStartupMessages({ library(ArchR); library(Matrix); library(data.table) })
## rec(): every value goes through as.character, so a reporting line cannot fail the run.
rec <- function(key, ...) {
  v <- vapply(list(...), function(x) tryCatch(paste(as.character(x), collapse = ","), error = function(e) "?"), "")
  cat(key, "\t", paste(v, collapse = "  "), "\n", sep = "")
}
OK <- TRUE; NCHK <- 0L
## isTRUE(): an NA or NaN comparison is a failure, never a pass.
check <- function(cond, msg) { NCHK <<- NCHK + 1L
  if (isTRUE(cond)) cat("  ok    ", msg, "\n", sep = "") else { cat("  FAIL  ", msg, "\n", sep = ""); OK <<- FALSE } }
ver <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) "unknown")
rec("version", "ArchR", ver("ArchR"), "Seurat", ver("Seurat"), "Matrix", ver("Matrix"),
    "data.table", ver("data.table"), "R", paste(R.version$major, R.version$minor, sep = "."))
rec("arch", "R built for", R.version$arch, "host", Sys.info()[["machine"]])
check(R.version$arch == Sys.info()[["machine"]], "R's binary architecture == the host's (not emulated)")
rec("blas", tryCatch(La_library(), error = function(e) "unknown"))
T0 <- proc.time()[["elapsed"]]; t0 <- T0
lap <- function(name) { t1 <- proc.time()[["elapsed"]]; rec("time", name, round(t1 - t0, 1), "s"); t0 <<- t1 }
P <- 1000000007
cksum <- function(m, wi, vj) {        # sum(x * (w_i * v_j mod P)) mod P, chunked so partial sums stay exact
  s <- summary(as(m, "TsparseMatrix"))
  term <- (s$x * ((wi[s$i] * vj[s$j]) %% P)) %% P
  acc <- 0
  for (b in split(seq_along(term), ceiling(seq_along(term) / 1e6))) acc <- (acc + sum(term[b]) %% P) %% P
  c(nnz = nrow(s), sum = sum(s$x), sumsq = sum(s$x^2), ck = acc)
}
md5f <- function(f) unname(tools::md5sum(f))

THREADS <- as.integer(Sys.getenv("ARCHR_THREADS", "8")); SEED <- 1L
addArchRThreads(threads = THREADS); addArchRLocking(locking = FALSE); set.seed(SEED)
rec("threads", THREADS)

## ---------------------------------------------------------------- annotation, from recount/'s tables
sizes <- fread("ref/chrom.sizes", header = FALSE, col.names = c("chr", "len"))
chromSizes <- GRanges(sizes$chr, IRanges(1, sizes$len)); seqlengths(chromSizes) <- sizes$len
bl <- read.table(gzfile("ref/blacklist.bed.gz"), sep = "\t", colClasses = c("character", "integer", "integer", "NULL"), col.names = c("chr", "s0", "e", "why"))
bl <- bl[bl$chr %in% sizes$chr, ]
blacklist <- GRanges(bl$chr, IRanges(bl$s0 + 1L, bl$e))
genomeAnnotation <- SimpleList(genome = "nullGenome", chromSizes = chromSizes, blacklist = blacklist)
g <- fread("ref/genes_archr.tsv")
gGR <- GRanges(g$chr, IRanges(g$start, g$end), strand = g$strand, gene_id = g$gene_id, symbol = make.unique(g$symbol))
ts <- fread("ref/tss_archr.tsv")
tssGR <- GRanges(ts$chr, IRanges(ts$pos, width = 1), strand = ts$strand, tx_id = seq_len(nrow(ts)), tx_name = ts$tx_id)
geneAnnotation <- SimpleList(genes = gGR, exons = gGR, TSS = tssGR)   # gene scores read genes only
cells <- readLines("ref/cells.txt")
vj <- (seq_along(cells) * 7919) %% 999979 + 1
rc <- fread("ref/cells_recount.tsv"); setkey(rc, barcode)
st <- fread("ref/matrix_stats.tsv")
rec("dataset", length(cells), "cells", nrow(sizes), "chroms", length(gGR), "genes", nrow(ts), "transcripts",
    length(blacklist), "blacklist regions")

## ---------------------------------------------------------------- 1. Arrow file
arrows <- createArrowFiles(inputFiles = c(pbmc10k = "fragments.tsv.gz"), sampleNames = "pbmc10k",
  validBarcodes = cells, geneAnnotation = geneAnnotation, genomeAnnotation = genomeAnnotation,
  minTSS = 0, minFrags = 1, maxFrags = 1e9, addTileMat = TRUE, TileMatParams = list(binarize = FALSE),
  addGeneScoreMat = TRUE, threads = THREADS, force = TRUE)
lap("createArrowFiles")
proj <- ArchRProject(ArrowFiles = arrows, outputDirectory = "proj", copyArrows = FALSE,
                     geneAnnotation = geneAnnotation, genomeAnnotation = genomeAnnotation, showLogo = FALSE)
cd <- as.data.frame(getCellColData(proj)); cd$barcode <- sub(".*#", "", rownames(cd))
r <- rc[cd$barcode]
check(nrow(cd) == length(cells) && setequal(cd$barcode, cells), "the project holds the 10,246 Cell Ranger cells, and nothing else")

## the recount is tied to Signac's run: per cell, its record count == signac-real's (asserted == CountFragments)
sg <- fread("signac/barcodes.tsv", header = FALSE, select = 1:2, col.names = c("barcode", "rec")); setkey(sg, barcode)
check(all(sg[cells]$rec == rc[cells]$rec_all), "recount records per cell == signac-real's recount (== Signac CountFragments), every cell")
check(all(cd$nFrags == r$kept), "nFrags == recount: fragments on the chroms, 10..2000 bp, every cell")
check(all(cd$nMonoFrags == r$mono & cd$nDiFrags == r$di & cd$nMultiFrags == r$multi), "nMono/nDi/nMultiFrags == trunc(width/147)+1 classes, every cell")
## ArchR stores ReadsInTSS as (n / 101) * 101, which is not always n in floating point.
check(all(cd$ReadsInTSS == (r$nW / 101) * 101) && max(abs(cd$ReadsInTSS - r$nW)) < 1e-9, "ReadsInTSS == insertion x TSS-window overlaps (as (n/101)*101), every cell")
tssE <- round(2 * (r$nW / 101) / pmax(r$nF / 100, 0.2), 3)
check(all(is.finite(cd$TSSEnrichment)) && all(cd$TSSEnrichment == tssE), "TSSEnrichment == 2(w/101)/max(f/100, 0.2) from the recount, exactly, every cell")
check(all(cd$ReadsInPromoter == r$promoter), "ReadsInPromoter == insertion x promoter overlaps, every cell")
check(all(cd$ReadsInBlacklist == r$blacklist), "ReadsInBlacklist == insertion x blacklist overlaps, every cell")
rec("qc", "records", sum(r$rec_all), "off_chroms", sum(r$rec_all - r$rec_chrom), "outside_10_2000", sum(r$rec_chrom - r$kept),
    "nFrags", sum(cd$nFrags), "median_nFrags", median(cd$nFrags), "median_TSSEnrichment", median(cd$TSSEnrichment),
    "cells_TSS_below_4", sum(cd$TSSEnrichment < 4), "ReadsInBlacklist", sum(cd$ReadsInBlacklist))

## ---------------------------------------------------------------- 2. TileMatrix, chrom by chrom
tchr <- setdiff(sizes$chr, c("chrM", "chrY"))
colTile <- setNames(numeric(length(cells)), cells); nbad <- 0L; ntiles <- 0; tot <- c(nnz = 0, sum = 0)
for (ch in tchr) {
  se <- getMatrixFromArrow(arrows, useMatrix = "TileMatrix", useSeqnames = ch, binarize = FALSE, verbose = FALSE)
  m <- drop0(assay(se)); j <- match(sub(".*#", "", colnames(m)), cells); idx <- rowData(se)$idx
  ntiles <- ntiles + nrow(m)
  if (nrow(m) != trunc(sizes$len[sizes$chr == ch] / 500) + 1 || anyNA(j)) nbad <- nbad + 1L
  c1 <- cksum(m, (idx * 40503) %% 999983 + 1, vj[j]); s1 <- st[matrix == "TileMatrix" & chr == ch]
  if (!(c1[["nnz"]] == s1$nnz && c1[["sum"]] == s1$sum && c1[["sumsq"]] == s1$sumsq && c1[["ck"]] == s1$ck)) {
    nbad <- nbad + 1L; rec("tile_mismatch", ch, c1, "recount", s1$nnz, s1$sum, s1$sumsq, s1$ck) }
  colTile[cells[j]] <- colTile[cells[j]] + colSums(m); tot <- tot + c1[c("nnz", "sum")]
  rm(se, m); invisible(gc())
}
lap("TileMatrix checks")
check(nbad == 0, "TileMatrix == recount per chrom (nnz, sum, sum of squares, checksum; blacklisted tiles zero), trunc(L/500)+1 tiles each")
check(all(colTile[cells] == rc[cells]$tile_ins), "TileMatrix column sums == recount tile insertions, every cell")
check(all(colTile[cells] + rc[cells]$blacklisted_tile_ins + rc[cells]$excluded_chr_ins == 2 * cd$nFrags[match(cells, cd$barcode)]),
      "conservation: TileMatrix column sum + blacklisted-tile + chrM/chrY insertions == 2 x nFrags, every cell")
rec("tile_matrix", "chroms", length(tchr), "tiles", ntiles, "nnz", tot[["nnz"]], "insertions", tot[["sum"]],
    "blacklisted_tile_insertions", sum(rc$blacklisted_tile_ins), "chrM_chrY_insertions", sum(rc$excluded_chr_ins))

## ---------------------------------------------------------------- 3. GeneScoreMatrix (#2334's precondition holds on real lengths)
gse <- getMatrixFromArrow(arrows, useMatrix = "GeneScoreMatrix", binarize = FALSE, verbose = FALSE)
gs <- assay(gse); gs <- gs[, paste0("pbmc10k#", cells)]
ngs <- sum(!(as.character(seqnames(gGR)) %in% c("chrM", "chrY")))
check(nrow(gs) == ngs && all(is.finite(gs@x)) && all(colSums(gs) > 0), "GeneScoreMatrix: one row per gene off chrM/chrY, every entry finite, every cell scored")
con <- file("gs.bin", "wb"); writeBin(as.integer(dim(gs)), con); writeBin(gs@i, con); writeBin(gs@p, con); writeBin(gs@x, con); close(con)
h_gs <- md5f("gs.bin"); unlink("gs.bin")
rec("gene_scores", "genes", nrow(gs), "nnz", length(gs@x), "colsum_range", signif(range(colSums(gs)), 8), "md5", h_gs)
rm(gse, gs); invisible(gc()); lap("GeneScoreMatrix checks")

## ---------------------------------------------------------------- 4. Cell Ranger's peaks as a PeakMatrix
pk <- fread("ref/peaks_archr.tsv")
pkGR <- GRanges(pk$chr, IRanges(pk$start, pk$end))
proj <- addPeakSet(proj, peakSet = pkGR, force = TRUE)
proj <- addPeakMatrix(proj, binarize = FALSE, ceiling = 4, force = TRUE)
lap("addPeakMatrix")
cd <- as.data.frame(getCellColData(proj)); cd$barcode <- sub(".*#", "", rownames(cd)); r <- rc[cd$barcode]
check(all(cd$ReadsInPeaks == r$peak_ins), "ReadsInPeaks == recount insertions in peaks (uncapped), every cell")
check(all(cd$FRIP == r$peak_ins / (2 * r$peak_chr_kept)), "FRIP == ReadsInPeaks / insertions on chroms with a peak, every cell")
nbad <- 0L
for (ch in unique(pk$chr)) {
  se <- getMatrixFromArrow(arrows, useMatrix = "PeakMatrix", useSeqnames = ch, binarize = FALSE, verbose = FALSE)
  m <- drop0(assay(se)); j <- match(sub(".*#", "", colnames(m)), cells); idx <- rowData(se)$idx
  c1 <- cksum(m, (idx * 40503) %% 999983 + 1, vj[j]); s1 <- st[matrix == "PeakMatrix" & chr == ch]
  if (!(c1[["nnz"]] == s1$nnz && c1[["sum"]] == s1$sum && c1[["sumsq"]] == s1$sumsq && c1[["ck"]] == s1$ck && max(m@x) <= 4)) {
    nbad <- nbad + 1L; rec("peak_mismatch", ch, c1, "recount", s1$nnz, s1$sum, s1$sumsq, s1$ck) }
  rm(se, m)
}
check(nbad == 0, "PeakMatrix == recount per chrom with ceiling 4 (nnz, sum, sum of squares, checksum)")
pst <- st[matrix == "PeakMatrix"]; pin <- st[matrix == "PeakInsertions"]
rec("peak_matrix", "peaks", nrow(pk), "nnz", sum(pst$nnz), "sum_capped", sum(pst$sum), "sum_uncapped", sum(pin$sum),
    "median_FRIP", signif(median(cd$FRIP), 6)); lap("PeakMatrix checks")

## ---------------------------------------------------------------- 5. iterative LSI and clusters (paths: reported)
proj <- addIterativeLSI(proj, useMatrix = "TileMatrix", name = "IterativeLSI", iterations = 2,
  clusterParams = list(resolution = c(0.2), sampleCells = 10000, n.start = 10),
  varFeatures = 25000, dimsToUse = 1:30, seed = SEED, threads = THREADS, force = TRUE)
lap("addIterativeLSI")
proj <- addClusters(proj, reducedDims = "IterativeLSI", method = "Seurat", name = "Clusters",
                    resolution = 0.8, seed = SEED, force = TRUE)
lap("addClusters")
lsi <- getReducedDims(proj, reducedDims = "IterativeLSI")
cl <- setNames(proj$Clusters, sub(".*#", "", proj$cellNames))[cells]
tab <- sort(table(cl), decreasing = TRUE)
rec("clusters", "n", length(tab), "sizes", paste(names(tab), as.integer(tab), sep = ":"))
rec("lsi", "dims", ncol(lsi), "cells", nrow(lsi))
ari <- function(a, b) { t <- table(a, b); c2 <- function(x) sum(x * (x - 1) / 2)
  s <- c2(t); ea <- c2(rowSums(t)); eb <- c2(colSums(t)); e <- ea * eb / c2(sum(t)); (s - e) / ((ea + eb) / 2 - e) }

## cross-code observation: Signac's LSI on the same bytes (this chip's signac-real run), clustered the same way
sl <- read.delim(gzfile("signac/lsi_u.tsv.gz")); se2 <- as.matrix(sl[match(cells, sl$barcode), 3:31]); rownames(se2) <- cells
se2 <- scale(se2)                                   # Signac's scale.embeddings, dims 2:30 (LSI_1 is depth)
sc <- tryCatch({
  nn <- Seurat::FindNeighbors(se2, k.param = 20, verbose = FALSE)
  as.character(Seurat::FindClusters(nn$snn, resolution = 0.8, random.seed = SEED, verbose = FALSE)[[1]])
}, error = function(e) { rec("signac_clusters_error", conditionMessage(e)); rep(NA_character_, length(cells)) })
sq <- fread("signac/qc.tsv"); sq <- sq[match(cells, sq$barcode)]
a <- cd[match(cells, cd$barcode), ]
rec("vs_signac", "clusters_ARI", signif(ari(cl, sc), 6), "signac_clusters", length(unique(sc)),
    "spearman_TSS", signif(cor(a$TSSEnrichment, sq$TSS, method = "spearman"), 6),
    "spearman_FRiP", signif(cor(a$FRIP, sq$FRiP, method = "spearman"), 6))

## ---------------------------------------------------------------- 6. marker genes per cluster (reported)
mk <- getMarkerFeatures(proj, useMatrix = "GeneScoreMatrix", groupBy = "Clusters",
  bias = c("TSSEnrichment", "log10(nFrags)"), testMethod = "wilcoxon", threads = THREADS)
lap("getMarkerFeatures")
fdr <- as.matrix(assays(mk)$FDR); lfc <- as.matrix(assays(mk)$Log2FC); gname <- rowData(mk)$name
## observations only: nothing below the tests may fail the run
mtab <- tryCatch({
canon <- c("MS4A1", "CD79A", "CD3E", "CD8A", "IL7R", "CD14", "LYZ", "FCGR3A", "NKG7", "GNLY", "CD1C", "PPBP")
mtab <- rbindlist(lapply(canon, function(x) { i <- match(x, gname); if (is.na(i)) return(NULL)
  ok <- !is.na(fdr[i, ]) & lfc[i, ] > 0; if (!any(ok)) return(NULL)
  k <- which(ok)[order(fdr[i, ok], -lfc[i, ok])[1]]     # the most significant cluster where it is up
  data.table(gene = x, cluster = colnames(fdr)[k], log2fc = signif(lfc[i, k], 6), fdr = signif(fdr[i, k], 6)) }))
}, error = function(e) { rec("markers_error", conditionMessage(e)); data.table(gene = character(0)) })
fwrite(mtab, "markers_canonical.tsv", sep = "\t")
for (k in seq_len(nrow(mtab))) rec("marker", mtab$gene[k], mtab$cluster[k], "log2FC", mtab$log2fc[k], "FDR", mtab$fdr[k])
npass <- tryCatch(colSums(!is.na(fdr) & fdr <= 0.01 & lfc >= 1), error = function(e) NA)
rec("markers", "genes_FDR<=0.01_log2FC>=1_per_cluster", paste(names(npass), npass, sep = ":"))

## ---------------------------------------------------------------- outputs for the cross-chip comparison
qc <- a[, c("barcode", "nFrags", "nMonoFrags", "nDiFrags", "nMultiFrags", "TSSEnrichment", "ReadsInTSS",
            "ReadsInPromoter", "ReadsInBlacklist", "ReadsInPeaks", "FRIP")]
fwrite(qc, "cell_qc.tsv", sep = "\t")
fwrite(data.table(barcode = cells, cluster = cl, signac_cluster = sc), "clusters.tsv", sep = "\t")
gz <- gzfile("lsi.tsv.gz", "w"); write.table(data.frame(barcode = sub(".*#", "", rownames(lsi)), signif(lsi, 10)), gz,
  sep = "\t", quote = FALSE, row.names = FALSE); close(gz)
rec("md5", "cell_qc", md5f("cell_qc.tsv"), "clusters", md5f("clusters.tsv"), "gene_scores", h_gs)
rec("peak_rss", tryCatch(gsub("[[:space:]]+", " ", grep("^VmHWM", readLines("/proc/self/status"), value = TRUE)), error = function(e) "?"))
rec("time", "archr_total", round(proc.time()[["elapsed"]] - T0, 1), "s")
cat(sprintf("checks: %d run, %s\n", NCHK, if (OK) "all ok" else "FAILURES"))
if (!OK) quit(status = 1)
cat("ARCHR OK\n")
