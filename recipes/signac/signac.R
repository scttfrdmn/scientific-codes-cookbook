## Signac on a planted scATAC fixture: every claim is an exact identity against an independent
## recount from the fragments file, a closed form from the planted design, or a planted label.
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
    "Rsamtools", ver("Rsamtools"), "RSpectra", ver("RSpectra"),
    "R", paste(R.version$major, R.version$minor, sep = "."))
rec("arch", R.version$arch)

## ---------------------------------------------------------------- fixture, read without Signac
fr <- read.table(gzfile("fragments.tsv.gz"), sep = "\t", quote = "",
                 col.names = c("chr", "start", "end", "cb", "n"),
                 colClasses = c("character", "integer", "integer", "character", "integer"))
fr$L <- fr$end - fr$start
truth <- read.table("truth.tsv", sep = "\t", header = TRUE, stringsAsFactors = FALSE)
pk    <- read.table("peaks.bed", sep = "\t", col.names = c("chr", "s0", "e0", "name", "kind"))
gn    <- read.table("genes.tsv", sep = "\t", header = TRUE, stringsAsFactors = FALSE)
cells <- truth$barcode
rec("fixture", nrow(fr), "records", length(unique(fr$cb)), "barcodes", length(cells), "cells",
    nrow(pk), "peaks", nrow(gn), "genes")

## ---------------------------------------------------------------- 1. CountFragments
cf <- CountFragments("fragments.tsv.gz", verbose = FALSE)
rownames(cf) <- cf$CB
bc <- sort(unique(fr$cb))
per <- function(v) { x <- tapply(v, factor(fr$cb, levels = bc), sum); x[is.na(x)] <- 0; x }
ind_freq <- per(rep(1L, nrow(fr))); ind_reads <- per(fr$n)
ind_nfr  <- per(fr$L < 147);       ind_mono  <- per(fr$L > 147 & fr$L < 294)
ind_hole <- per(fr$L == 147 | fr$L == 294); ind_di <- per(fr$L >= 295)
check(setequal(cf$CB, bc), "CountFragments does not report every barcode in the file")
cf <- cf[bc, ]
check(all(cf$frequency_count == ind_freq), "frequency_count != number of fragment records per barcode")
check(all(cf$reads_count == ind_reads), "reads_count != sum of the duplicate-count column")
check(all(cf$nucleosome_free == ind_nfr), "nucleosome_free != fragments < 147 bp")
check(all(cf$mononucleosomal == ind_mono), "mononucleosomal != fragments of 148..293 bp")
## Conservation: every record is in exactly one of nfr / mono / >=295 / {147, 294}. The last bin
## exists because Signac's C++ uses strict inequalities at both ends (147 < L < 294), so a
## fragment of exactly 147 or 294 bp is in NEITHER bin, while its docs say "147-294 bp".
check(all(cf$frequency_count == cf$nucleosome_free + cf$mononucleosomal + ind_di + ind_hole),
      "fragment records are not conserved across the length bins")
rec("count_fragments", "barcodes", nrow(cf), "records", sum(cf$frequency_count),
    "reads", sum(cf$reads_count), "nfr", sum(cf$nucleosome_free), "mono", sum(cf$mononucleosomal),
    "di", sum(ind_di), "len147or294_in_neither_bin", sum(ind_hole))

## ---------------------------------------------------------------- 2. FeatureMatrix
cellv <- setNames(cells, cells)
frag  <- CreateFragmentObject("fragments.tsv.gz", cells = cellv, verbose = FALSE)
peaks <- GRanges(pk$chr, IRanges(pk$s0 + 1L, pk$e0))           # BED -> 1-based closed
pnames <- suppressWarnings(GRangesToString(peaks))   # deprecated in 1.17, still the row-name format
fm <- FeatureMatrix(frag, features = peaks, cells = cells, verbose = FALSE)
fm7 <- FeatureMatrix(frag, features = peaks, cells = cells, process_n = 7, verbose = FALSE)
## Independent recount: a fragment [start, end) counts once for a peak [s0, e0) it overlaps
## (half-open on both), whatever its duplicate count, and only for the 200 cells.
fc <- fr[fr$cb %in% cells, ]
ind <- matrix(0L, nrow(pk), length(cells), dimnames = list(pnames, cells))
for (i in seq_len(nrow(pk))) {
  hit <- fc$chr == pk$chr[i] & fc$start < pk$e0[i] & fc$end > pk$s0[i]
  t <- table(factor(fc$cb[hit], levels = cells)); ind[i, ] <- as.integer(t)
}
fmd <- as.matrix(fm)[pnames, cells]
check(identical(dim(fmd), dim(ind)), "FeatureMatrix has the wrong shape")
check(all(fmd == ind), "FeatureMatrix != independent half-open overlap count")
check(all(as.matrix(fm7)[pnames, cells] == fmd), "FeatureMatrix changes with process_n chunking")
touch <- sum(sapply(seq_len(nrow(pk)), function(i)
  sum(fc$chr == pk$chr[i] & (fc$end == pk$s0[i] | fc$start == pk$e0[i]))))
rec("feature_matrix", "peaks", nrow(fmd), "cells", ncol(fmd), "total", sum(fmd),
    "cells_mismatch", sum(fmd != ind), "touching_records_excluded", touch)

## ---------------------------------------------------------------- 3. object, FRiP, nucleosome signal
anno <- GRanges(gn$seqnames, IRanges(gn$start, gn$end), strand = gn$strand,
                gene_name = gn$gene_name, gene_id = gn$gene_id, gene_biotype = gn$gene_biotype,
                tx_id = gn$tx_id, type = gn$type)
ca  <- CreateChromatinAssay(counts = fm, sep = c("-", "-"), fragments = frag, annotation = anno,
                            verbose = FALSE)
obj <- CreateSeuratObject(counts = ca, assay = "peaks")
obj$total <- cf[colnames(obj), "frequency_count"]
obj <- FRiP(obj, assay = "peaks", total.fragments = "total", verbose = FALSE)
frip_ind <- colSums(ind)[colnames(obj)] / ind_freq[colnames(obj)]
check(all(obj$FRiP == frip_ind), "FRiP != peak fragments / total fragments")
obj <- suppressWarnings(NucleosomeSignal(obj, verbose = FALSE))
ns_ind <- ind_mono[colnames(obj)] / ind_nfr[colnames(obj)]
check(all(obj$nucleosome_signal == ns_ind), "nucleosome_signal != mono / nucleosome-free")
rec("frip", "min", signif(min(obj$FRiP), 6), "max", signif(max(obj$FRiP), 6))
rec("nucleosome_signal", "min", signif(min(obj$nucleosome_signal), 6),
    "max", signif(max(obj$nucleosome_signal), 6))

## ---------------------------------------------------------------- 4. TSS enrichment
## Planted closed form: (cuts in the central 1001 bp / 1001) / (cuts in the 2 x 100 bp flanks / 200),
## summed over the four protein-coding TSSs; the lncRNA decoy carries 60 central cuts per cell
## and must be excluded by the biotype filter.
obj <- suppressWarnings(TSSEnrichment(obj, fast = TRUE, verbose = FALSE))
tss_fast <- obj$TSS.enrichment
obj <- suppressWarnings(TSSEnrichment(obj, fast = FALSE, verbose = FALSE))
tss_full <- obj$TSS.enrichment
tr <- truth[match(colnames(obj), truth$barcode), ]
planted <- (tr$tss_center_cuts / 1001) / (tr$tss_flank_cuts / 200)
## Independent recount from the file: both ends of every chr2 fragment are cuts.
pc <- gn[gn$gene_biotype == "protein_coding", ]
tpos <- ifelse(pc$strand == "+", pc$start - 1L, pc$end - 1L)
f2 <- fr[fr$chr == "chr2" & fr$cb %in% cells, ]
cuts <- data.frame(pos = c(f2$start, f2$end), cb = c(f2$cb, f2$cb))
d <- outer(cuts$pos, tpos, "-"); ad <- apply(abs(d), 1, min)
cc <- tapply(ad <= 500, factor(cuts$cb, levels = colnames(obj)), sum)
ff <- tapply(ad >= 901 & ad <= 1000, factor(cuts$cb, levels = colnames(obj)), sum)
recount <- (cc / 1001) / (ff / 200)
rel <- function(a, b) max(abs(a - b) / abs(b))
check(all(cc == tr$tss_center_cuts) && all(ff == tr$tss_flank_cuts),
      "the file's TSS cuts do not match the planted design")
check(rel(tss_fast, planted) <= 1e-12, "TSSEnrichment(fast=TRUE) != planted closed form")
check(rel(tss_full, planted) <= 1e-12, "TSSEnrichment(fast=FALSE) != planted closed form")
pa <- tr$population == "A"
check(min(tss_fast[pa]) > max(tss_fast[!pa]), "TSS enrichment does not separate the planted populations")
rec("tss_enrichment", "fast_vs_planted_maxrel", signif(rel(tss_fast, planted), 3),
    "full_vs_planted_maxrel", signif(rel(tss_full, planted), 3),
    "A_range", signif(range(tss_fast[pa]), 5), "B_range", signif(range(tss_fast[!pa]), 5))

## ---------------------------------------------------------------- 5. GeneActivity
ga <- GeneActivity(obj, verbose = FALSE)
win_s0 <- ifelse(pc$strand == "+", pc$start - 1L - 2000L, pc$start - 1L)  # 2 kb upstream,
win_e0 <- ifelse(pc$strand == "+", pc$end, pc$end + 2000L)                # strand-aware
ga_ind <- t(sapply(seq_len(nrow(pc)), function(i) {
  hit <- f2$start < win_e0[i] & f2$end > win_s0[i]
  as.integer(table(factor(f2$cb[hit], levels = colnames(obj))))
}))
rownames(ga_ind) <- pc$gene_name
check(setequal(rownames(ga), pc$gene_name), "GeneActivity genes != the protein-coding genes")
check(all(as.matrix(ga)[pc$gene_name, colnames(obj)] == ga_ind),
      "GeneActivity != fragments overlapping gene body + 2 kb upstream (strand-aware)")
rec("gene_activity", "genes", nrow(ga), "total", sum(ga))

## ---------------------------------------------------------------- 6. TF-IDF (method = 1)
obj <- RunTFIDF(obj, method = 1, verbose = FALSE)
tfidf <- as.matrix(LayerData(obj, assay = "peaks", layer = "data"))[pnames, colnames(obj)]
X <- ind[, colnames(obj)]
closed <- log1p(t(t(X) / colSums(X)) * (ncol(X) / rowSums(X)) * 1e4)
tf_err <- max(abs(tfidf - closed))
## Not bit-identical by construction: Signac multiplies by 1/colSums where the closed form divides.
check(tf_err <= 1e-12, "RunTFIDF(method=1) != log1p(tf * idf * 1e4)")
rec("tfidf", "method", 1, "max_abs_diff", signif(tf_err, 3))

## ---------------------------------------------------------------- 7. LSI (RunSVD) and separation
lsi_run <- function(o) {
  o <- FindTopFeatures(o, min.cutoff = "q0", verbose = FALSE)
  set.seed(20261016)
  RunSVD(o, n = 10, verbose = FALSE)
}
obj <- lsi_run(obj)
m <- Misc(obj[["lsi"]])
A <- t(tfidf[VariableFeatures(obj), , drop = FALSE])
d_ref <- svd(A, nu = 0, nv = 0)$d[1:10]
sv_rel <- max(abs(m$d - d_ref) / d_ref)
resid <- max(sapply(1:10, function(k) sqrt(sum((A %*% m$v[, k] - m$d[k] * m$u[, k])^2)) / m$d[k]))
check(sv_rel <= 1e-5, "RunSVD singular values != base::svd beyond Signac's requested tol (1e-5)")
check(resid <= 1e-5, "RunSVD's (u, d, v) do not satisfy A v = d u")
ari <- function(a, b) {
  t <- table(a, b); s <- function(x) sum(choose(x, 2))
  n <- sum(t); e <- s(rowSums(t)) * s(colSums(t)) / choose(n, 2)
  (s(t) - e) / ((s(rowSums(t)) + s(colSums(t))) / 2 - e)
}
km <- function(o) { set.seed(1); kmeans(Embeddings(o[["lsi"]])[, 2:10], centers = 2, nstart = 25)$cluster }
lab <- truth$population[match(colnames(obj), truth$barcode)]
ari_lsi <- ari(km(obj), lab)
## Control: permute every peak's row across cells -- peak totals (and so IDF) unchanged,
## population structure destroyed.
set.seed(7)
Xs <- t(apply(X, 1, sample)); dimnames(Xs) <- dimnames(X)
ctl <- CreateSeuratObject(counts = CreateAssayObject(counts = as(Xs, "CsparseMatrix")), assay = "peaks")
ctl <- RunTFIDF(ctl, method = 1, verbose = FALSE)
ctl <- lsi_run(ctl)
ari_ctl <- ari(km(ctl), lab)
check(ari_lsi == 1, "LSI + k-means does not recover the planted populations exactly")
check(abs(ari_ctl) < 0.1, "the permuted control shows structure it cannot have")
dc <- cor(Embeddings(obj[["lsi"]])[, 1], obj$nCount_peaks)
rec("lsi", "singular_values", signif(m$d, 8))
rec("lsi", "sv_maxrel_vs_svd", signif(sv_rel, 3), "residual", signif(resid, 3),
    "ari_planted", signif(ari_lsi, 6), "ari_permuted_control", signif(ari_ctl, 4),
    "cor_LSI1_depth", signif(dc, 4))

## ---------------------------------------------------------------- 8. AccessiblePeaks
Idents(obj) <- setNames(factor(lab), colnames(obj))
ap <- AccessiblePeaks(obj, idents = "A", min.cells = 10)
ap_ind <- rownames(X)[rowSums(X[, lab == "A"] > 0) > 10]
kind <- setNames(pk$kind, pnames)
check(setequal(ap, ap_ind), "AccessiblePeaks != peaks with a fragment in > 10 A cells")
check(all(names(kind)[kind == "A"] %in% ap), "an A-only peak is missing from A's accessible peaks")
check(!any(names(kind)[kind == "B"] %in% ap), "a B-only peak is accessible in A")
rec("accessible_peaks_A", length(ap), "A_only", sum(kind[ap] == "A"), "shared",
    sum(kind[ap] == "shared"), "B_only", sum(kind[ap] == "B"))

write.table(data.frame(barcode = colnames(obj), population = lab, FRiP = obj$FRiP,
                       nucleosome_signal = obj$nucleosome_signal, TSS_fast = tss_fast,
                       TSS_full = tss_full, TSS_planted = planted),
            "qc.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
invisible(writeMM(as(fm, "CsparseMatrix"), "peaks.mtx"))
if (!OK) quit(status = 1)
cat("SIGNAC OK\n")
