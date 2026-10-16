suppressPackageStartupMessages({ library(ArchR); library(Matrix); library(data.table) })
# Every reported line goes to stdout AND smoke-check.txt. Values are formatted in one place, so a
# line whose only job is to describe the run cannot fail it (format() is wrapped in tryCatch).
say <- function(s) { cat(s); cat(s, file = "smoke-check.txt", append = TRUE) }
rec <- function(k, ...) say(sprintf("%-28s %s\n", k, paste(sapply(list(...), function(v)
  tryCatch(paste(format(v), collapse = " "), error = function(e) "?")), collapse = "  ")))
fails <- character(0)
check <- function(ok, msg) { ok <- isTRUE(ok); if (!ok) fails <<- c(fails, msg); say(paste0(if (ok) "  ok    " else "  FAIL  ", msg, "\n")) }
vers <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) "unknown")
rec("version", "ArchR", vers("ArchR"), "R", as.character(getRversion()), "data.table", vers("data.table"))
rec("arch", "R built for", R.version$arch, "host", Sys.info()[["machine"]])
check(R.version$arch == Sys.info()[["machine"]], "R's binary architecture == the host's (not emulated)")

THREADS <- 1L; SEED <- 1L
addArchRThreads(threads = THREADS)
addArchRLocking(locking = FALSE)
set.seed(SEED)

# ---------------- the planted design, read from the staged truth tables ----------------
genome <- fread("genome.tsv"); cells <- fread("cells.tsv"); genes <- fread("genes.tsv")
tiles <- fread("tiles.tsv"); black <- fread("blacklist.tsv")
chromSizes <- GRanges(genome$chr, IRanges(1, genome$length)); seqlengths(chromSizes) <- genome$length
blacklist <- GRanges(black$chr, IRanges(black$start, black$end))
genomeAnnotation <- SimpleList(genome = "nullGenome", chromSizes = chromSizes, blacklist = blacklist)
gGR <- GRanges(genes$chr, IRanges(genes$start, genes$end), strand = genes$strand,
               gene_id = genes$symbol, symbol = genes$symbol)
tssGR <- resize(gGR, 1, fix = "start"); mcols(tssGR) <- DataFrame(tx_id = seq_along(tssGR), tx_name = genes$symbol)
geneAnnotation <- SimpleList(genes = gGR, exons = gGR, TSS = tssGR)

# ---------------- 1. Arrow files ----------------
t0 <- Sys.time()
arrows <- createArrowFiles(
  inputFiles = c(planted = "planted.fragments.tsv.gz", flat = "flat.fragments.tsv.gz"),
  sampleNames = c("planted", "flat"),
  geneAnnotation = geneAnnotation, genomeAnnotation = genomeAnnotation,
  minTSS = 0, minFrags = 500, addTileMat = TRUE, TileMatParams = list(binarize = FALSE),
  addGeneScoreMat = TRUE, threads = THREADS, force = TRUE)
rec("arrows", paste(arrows, collapse=","), "seconds", round(as.numeric(difftime(Sys.time(), t0, units="secs")), 1))
check(all(file.exists(c("planted.arrow", "flat.arrow"))), "both Arrow files written")

projAll <- ArchRProject(ArrowFiles = c("planted.arrow", "flat.arrow"), outputDirectory = "proj",
                        copyArrows = FALSE, geneAnnotation = geneAnnotation, genomeAnnotation = genomeAnnotation,
                        showLogo = FALSE)
cd <- as.data.frame(getCellColData(projAll))
cd$barcode <- sub(".*#", "", rownames(cd))
m <- merge(cd, as.data.frame(cells), by = "barcode", suffixes = c("", ".planted"))
rec("cells", "planted", nrow(cells), "in project", nrow(cd), "matched", nrow(m))
check(nrow(m) == nrow(cells) && nrow(cd) == nrow(cells), "every planted cell is in the project, and nothing else")
check(all(m$nFrags == m$nFrags.planted), "nFrags == planted in-range fragment count, every cell")
check(all(m$nMonoFrags == m$nMono & m$nDiFrags == m$nDi & m$nMultiFrags == m$nMulti),
      "nMono/nDi/nMultiFrags == planted size classes, every cell")
rec("nFrags", "planted range", range(m$nFrags.planted), "out-of-window planted per cell", range(m$nOutOfRange))

# ---------------- independent recount from the raw fragment text (no ArchR) ----------------
fr <- rbind(fread("planted.fragments.tsv.gz", header = FALSE, col.names = c("chr","s0","e","bc","n")),
            fread("flat.fragments.tsv.gz",    header = FALSE, col.names = c("chr","s0","e","bc","n")))
fr[, w := e - s0]
fr <- fr[w >= 10 & w <= 2000]                       # ArchR's documented minFragSize/maxFragSize
ins <- rbind(fr[, .(chr, pos = s0 + 1L, bc)], fr[, .(chr, pos = e, bc)])   # Tn5 insertions, 1-based
nf <- fr[, .N, by = bc]; setkey(nf, bc)
check(all(nf[m$barcode]$N == m$nFrags), "independent recount of in-window fragments == nFrags")

# TSS enrichment, recomputed from ArchR's published definition: insertions in a 101 bp window at
# each TSS over insertions in the two 100 bp flanks at +-(1901..2000), 2*(w/101)/max(f/100, 0.2).
tss <- data.table(chr = as.character(seqnames(tssGR)), t = start(tssGR))
near <- function(chr, pos) {                        # signed distance to the nearest TSS on chr
  out <- rep(NA_integer_, length(pos))
  for (c in unique(chr)) { i <- which(chr == c); tt <- sort(tss[chr == c]$t)
    j <- findInterval(pos[i], tt); lo <- tt[pmax(j, 1)]; hi <- tt[pmin(j + 1, length(tt))]
    out[i] <- ifelse(abs(pos[i] - lo) <= abs(pos[i] - hi), pos[i] - lo, pos[i] - hi) }
  out }
ins[, d := near(chr, pos)]
tc <- ins[, .(nW = sum(abs(d) <= 50), nF = sum(abs(d) >= 1901 & abs(d) <= 2000)), by = bc]; setkey(tc, bc)
tc[, tssE := round(2 * (nW / 101) / pmax(nF / 100, 0.2), 3)]
ti <- tc[m$barcode]
check(all(is.finite(m$TSSEnrichment)), "TSSEnrichment is finite for every cell (no NaN)")
check(max(abs(ti$nW - m$ReadsInTSS)) < 1e-6, "ReadsInTSS == independent count of insertions within 50 bp of a TSS")
check(max(abs(ti$tssE - m$TSSEnrichment)) == 0, "TSSEnrichment == independent recompute, exactly")
medP <- median(m$TSSEnrichment[m$sample == "planted"]); medF <- median(m$TSSEnrichment[m$sample == "flat"])
rec("TSSEnrichment median", "planted", medP, "flat control", medF, "ratio", round(medP / medF, 2))
check(medP >= 5 * medF, "planted cells' TSS enrichment >= 5x the background-only control")
# promoter (TSS -2000..+100, stranded) and blacklist insertion counts
prom <- data.table(chr = genes$chr, lo = ifelse(genes$strand == "+", genes$start - 2000, genes$end - 100),
                   hi = ifelse(genes$strand == "+", genes$start + 100, genes$end + 2000))
inAny <- function(chr, pos, R) { r <- rep(FALSE, length(pos))
  for (k in seq_len(nrow(R))) r <- r | (chr == R$chr[k] & pos >= R$lo[k] & pos <= R$hi[k]); r }
ins[, inP := inAny(chr, pos, prom)]
ins[, inB := inAny(chr, pos, data.table(chr = black$chr, lo = black$start, hi = black$end))]
pc <- ins[, .(p = sum(inP), b = sum(inB)), by = bc]; setkey(pc, bc); pi <- pc[m$barcode]
check(all(pi$p == m$ReadsInPromoter), "ReadsInPromoter == independent count")
check(all(pi$b == m$ReadsInBlacklist), "ReadsInBlacklist == independent count")
rec("ReadsInBlacklist", "total", sum(m$ReadsInBlacklist))

# ---------------- 2. TileMatrix: every entry, against the independent count ----------------
tm <- getMatrixFromProject(projAll, useMatrix = "TileMatrix", binarize = FALSE)
TM <- assay(tm); rdt <- as.data.frame(rowData(tm)); colnames(TM) <- sub(".*#", "", colnames(TM))
key <- sprintf("%s:%d", rdt$seqnames, as.integer(rdt$start))
ins[, tkey := sprintf("%s:%d", chr, as.integer((pos %/% 500L) * 500L))]
blk <- unlist(lapply(seq_len(nrow(black)), function(k) sprintf("%s:%d", black$chr[k], as.integer(seq(black$start[k], black$end[k] - 499, by = 500)))))
ic <- ins[!(tkey %in% blk), .N, by = .(tkey, bc)]
MY <- sparseMatrix(i = match(ic$tkey, key), j = match(ic$bc, colnames(TM)), x = ic$N, dims = dim(TM))
rec("TileMatrix", "tiles", nrow(TM), "cells", ncol(TM), "sum", sum(TM), "blacklisted tiles", length(blk),
    "planted insertions in them", ins[tkey %in% blk, .N])
check(nrow(TM) == sum(genome$length %/% 500 + 1), "one row per 500 bp tile, trunc(L/500)+1 per chromosome")
check(!anyNA(match(ic$tkey, key)), "every insertion lands on a tile ArchR defines")
check(sum(abs(TM - MY)) == 0, "TileMatrix == independent per-tile insertion count, every entry")
check(sum(TM[key %in% blk, ]) == 0 && ins[tkey %in% blk, .N] > 0, "blacklisted tiles are zero although insertions land there")
rm(ins, fr, ic, MY, TM, tm); invisible(gc())

gsm <- assay(getMatrixFromProject(projAll, useMatrix = "GeneScoreMatrix"))
rec("GeneScoreMatrix", "genes", nrow(gsm), "cells", ncol(gsm), "non-finite entries", sum(!is.finite(gsm@x)))
check(nrow(gsm) == nrow(genes) && all(is.finite(gsm@x)) && all(Matrix::colSums(gsm) > 0),
      "GeneScoreMatrix: one row per gene, every entry finite, every cell scored")
rm(gsm); invisible(gc())

# ---------------- 3. LSI + clustering on the planted sample ----------------
proj <- projAll[projAll$Sample == "planted", ]
truth <- setNames(cells$pop, cells$barcode)[sub(".*#", "", proj$cellNames)]
proj$truth <- truth
t1 <- Sys.time()
proj <- addIterativeLSI(proj, useMatrix = "TileMatrix", name = "IterativeLSI", iterations = 2,
                        varFeatures = 25000, dimsToUse = 1:30, seed = SEED, threads = THREADS, force = TRUE)
proj <- addClusters(proj, reducedDims = "IterativeLSI", method = "Seurat", name = "Clusters",
                    resolution = 0.8, seed = SEED, force = TRUE)
rec("LSI+clusters seconds", round(as.numeric(difftime(Sys.time(), t1, units="secs")), 1))
ari <- function(a, b) { t <- table(a, b); n <- sum(t); c2 <- function(x) sum(x * (x - 1) / 2)
  s <- c2(t); ea <- c2(rowSums(t)); eb <- c2(colSums(t)); e <- ea * eb / c2(n)
  (s - e) / ((ea + eb) / 2 - e) }
cl <- proj$Clusters
say(paste0(paste(capture.output(print(table(cluster = cl, planted = truth))), collapse = "\n"), "\n"))
A1 <- ari(cl, truth); set.seed(SEED); A0 <- ari(cl, sample(truth))
rec("ARI", "clusters vs planted", sprintf("%.6f", A1), "vs shuffled labels", sprintf("%.6f", A0))
check(is.finite(A1) && A1 == 1, "clusters recover the two planted populations exactly (ARI == 1)")
check(is.finite(A0) && abs(A0) < 0.05, "control: shuffled labels give ARI near 0")
lsi <- getReducedDims(proj, reducedDims = "IterativeLSI")
fwrite(data.table(cell = rownames(lsi), round(lsi[, 1:5], 6)), "lsi_dims1-5.tsv", sep = "\t")
fwrite(data.table(cell = proj$cellNames, truth = truth, cluster = cl), "clusters.tsv", sep = "\t")

# ---------------- 4. marker tiles and genes ----------------
# BH at FDR 0.01 controls the false-discovery PROPORTION: among ~200 true markers, one or two
# background features passing is expected, not a defect. So the claim is recall plus rank:
# every planted feature passes, and the planted features are exactly the top of the ranking.
mk <- function(mat, groupBy) getMarkerFeatures(proj, useMatrix = mat, groupBy = groupBy,
        bias = c("TSSEnrichment", "log10(nFrags)"), testMethod = "wilcoxon", binarize = (mat == "TileMatrix"),
        threads = THREADS)
rank_check <- function(se, what, ids, want_by_pop) {
  for (p in names(want_by_pop)) {
    fdr <- assays(se)$FDR[, p]; lfc <- assays(se)$Log2FC[, p]; want <- want_by_pop[[p]]
    pass <- ids[!is.na(fdr) & fdr <= 0.01 & lfc >= 1]
    up <- which(!is.na(fdr) & lfc > 0); o <- up[order(fdr[up], -lfc[up])]
    top <- ids[o][seq_along(want)]
    worstP <- max(fdr[ids %in% want]); bestO <- suppressWarnings(min(fdr[up][!(ids[up] %in% want)]))
    rec(sprintf("marker %s %s", what, p), "planted", length(want), "pass", sum(want %in% pass),
        "off-target passing", sum(!(pass %in% want)), "worst planted FDR", signif(worstP, 3),
        "best off-target FDR", signif(bestO, 3))
    check(all(want %in% pass), sprintf("every planted %s of population %s passes FDR<=0.01 & Log2FC>=1", what, p))
    check(setequal(top, want), sprintf("population %s's top %d %ss by FDR are exactly the planted ones", p, length(want), what))
  }
}
tse <- mk("TileMatrix", "truth"); trd <- rowData(tse)
tid <- sprintf("%s:%d", trd$seqnames, as.integer(trd$start))
tw <- lapply(c(A = "A", B = "B"), function(p) sprintf("%s:%d", tiles$chr, as.integer(tiles$tile_start))[tiles$pop == p])
rank_check(tse, "tile", tid, tw)
gse <- mk("GeneScoreMatrix", "truth")
rank_check(gse, "gene", rowData(gse)$name, lapply(c(A = "A", B = "B"), function(p) genes$symbol[genes$pop == p]))
set.seed(SEED); proj$shuffled <- sample(truth)
sse <- mk("TileMatrix", "shuffled")
ns <- sum(!is.na(assays(sse)$FDR) & assays(sse)$FDR <= 0.01 & assays(sse)$Log2FC >= 1)
rec("control: shuffled labels", "marker tiles passing", ns, "min FDR", signif(min(assays(sse)$FDR, na.rm = TRUE), 3))
check(ns == 0, "control: shuffled labels give no marker tiles")
fwrite(data.table(tile = tid, FDR_A = assays(tse)$FDR[, "A"], Log2FC_A = assays(tse)$Log2FC[, "A"],
                  FDR_B = assays(tse)$FDR[, "B"], Log2FC_B = assays(tse)$Log2FC[, "B"]), "markers_tiles.tsv", sep = "\t")

fwrite(as.data.table(m[, c("barcode","Sample","pop","nFrags","nMonoFrags","nDiFrags","nMultiFrags","TSSEnrichment","ReadsInTSS","ReadsInPromoter","ReadsInBlacklist")]),
       "cell_qc.tsv", sep = "\t")
hwm <- tryCatch(grep("^VmHWM", readLines("/proc/self/status"), value = TRUE), error = function(e) "?")
rec("peak RSS", gsub("[[:space:]]+", " ", paste(hwm, collapse = " ")))
if (length(fails)) { say(sprintf("FAILED: %d check(s)\n", length(fails))); quit(status = 1) }
say("ArchR OK\n")
