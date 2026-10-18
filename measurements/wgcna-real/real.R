# WGCNA on a whole TCGA cohort: every BRCA primary tumour in the PanCanAtlas RNA-seq matrix, every
# expressed gene, one signed network in one block -- held to the recipe's identities at that size.
#
#   Rscript real.R <data dir> <out dir> <threads> <expected genes> <expected samples>
#
# The cohort: BRCA, sample type 01 (primary solid tumour), not flagged Do_not_use, one aliquot per
# patient. The genes: no missing value in the cohort, median log2(RSEM + 1) >= 3. Network: signed,
# power 12 (the WGCNA FAQ's value for a signed network on >= 40 samples), minModuleSize 30,
# mergeCutHeight 0.25 -- the tutorial's settings -- in ONE block, so the TOM is exact over all genes.
#
# What is asserted (a failure fails the run): WGCNA::cor and the signed adjacency against their
# closed forms over the whole gene x gene matrix, and stats::cor on 200 seeded rows; the TOM
# against (L + A)/(min(k_i,k_j) + 1 - A) on 500 seeded rows of the full TOM; the TOM at 1 vs N threads; blockwiseModules twice; every
# eigengene against PC1 of its scaled module; kWithin against its definition; three marker gene sets
# of the breast-tumour intrinsic clusters (proliferation, luminal/ER, T cell) each landing in its
# own module; and a control with every gene permuted independently. Reported: the soft-threshold
# curve, module sizes, hub genes, and the default maxBlockSize = 5000 blocked run against one block.
options(warn = 1, stringsAsFactors = FALSE)
args <- commandArgs(trailingOnly = TRUE)
DATA <- args[1]; OUT <- args[2]; NT <- as.integer(args[3])
WANT_G <- as.integer(args[4]); WANT_S <- as.integer(args[5])
dir.create(OUT, showWarnings = FALSE)
SCORE <- file.path(OUT, "score.tsv"); LOG <- file.path(OUT, "real.log")
cat("observable\tvalue\n", file = SCORE)
say <- function(...) { m <- sprintf(...); cat(m, "\n", sep = ""); cat(m, "\n", sep = "", file = LOG, append = TRUE) }
# every value through as.character, so a tuple-like value can never become format arguments
rec <- function(k, v) {
  v <- paste(vapply(as.list(v), function(e) as.character(e), ""), collapse = " ")
  cat(sprintf("%s\t%s\n", k, v), file = SCORE, append = TRUE); say("%s\t%s", k, v)
}
fails <- character()
check <- function(k, ok, v) {   # a NaN or NA is a failure, never a pass
  ok <- isTRUE(as.logical(ok)); rec(k, c(v, if (ok) "ok" else "FAIL"))
  if (!ok) fails <<- c(fails, k)
}
die <- function(msg) { say("FAIL: %s", msg); quit(status = 1) }
le <- function(x, tol) isTRUE(is.finite(x) && x <= tol)
fe <- function(x) sprintf("%.3e", x)
T0 <- proc.time()[["elapsed"]]
lap <- function(k) { t <- proc.time()[["elapsed"]]; rec(paste0("time_", k), sprintf("%.1f s", t - T0)); T0 <<- t }
hwm <- function() {
  l <- grep("^VmHWM", readLines("/proc/self/status"), value = TRUE)
  sprintf("%.2f GiB", as.numeric(gsub("[^0-9]", "", l)) / 1048576)
}
ver <- function(p) tryCatch(as.character(utils::packageVersion(p)), error = function(e) "unknown")
ari <- function(a, b) {
  t <- table(a, b); n <- sum(t); c2 <- function(x) sum(x * (x - 1) / 2)
  s <- c2(t); sa <- c2(rowSums(t)); sb <- c2(colSums(t)); e <- sa * sb / (n * (n - 1) / 2)
  (s - e) / ((sa + sb) / 2 - e)
}
nonGrey <- function(cl) length(setdiff(unique(cl), 0))
sizes <- function(cl) paste(sort(as.integer(table(cl[cl != 0])), decreasing = TRUE), collapse = ",")

suppressPackageStartupMessages({ library(WGCNA); library(data.table) })
rec("versions", sprintf("WGCNA %s dynamicTreeCut %s fastcluster %s data.table %s R %s.%s",
    ver("WGCNA"), ver("dynamicTreeCut"), ver("fastcluster"), ver("data.table"), R.version$major, R.version$minor))
rec("r_arch", R.version$arch)
rec("blas", La_library())
enableWGCNAThreads(nThreads = NT)
rec("wgcna_threads", WGCNAnThreads())

# ------------------------------------------------------------------------------------------------
# The matrix and the cohort
# ------------------------------------------------------------------------------------------------
dt <- fread(file.path(DATA, "EBPlusPlusAdjustPANCAN_IlluminaHiSeq_RNASeqV2.geneExp.tsv"),
            sep = "\t", header = TRUE, na.strings = c("NA", ""), showProgress = FALSE)
genes <- dt[[1]]; bc <- names(dt)[-1]
if (length(genes) != WANT_G || length(bc) != WANT_S)
  die(sprintf("matrix is %d genes x %d samples, the release is %d x %d", length(genes), length(bc), WANT_G, WANT_S))
if (anyDuplicated(genes)) die("duplicate gene ids")
if (anyDuplicated(bc)) die("duplicate sample barcodes")
rec("matrix_genes_samples", c(length(genes), length(bc)))

ann <- fread(file.path(DATA, "merged_sample_quality_annotations.tsv"), sep = "\t", header = TRUE,
             colClasses = "character", showProgress = FALSE)
key <- c("aliquot_barcode", "cancer type", "Do_not_use")
annR <- unique(ann[platform %in% c("IlluminaHiSeq_RNASeqV2", "IlluminaGA_RNASeqV2"), key, with = FALSE])
if (anyDuplicated(annR$aliquot_barcode)) die("an RNA-seq aliquot carries conflicting annotations")
m <- match(bc, annR$aliquot_barcode)
code <- as.integer(substr(bc, 14, 15)); patient <- substr(bc, 1, 12)
brca <- which(!is.na(m) & annR[["cancer type"]][m] == "BRCA" & code == 1 &
              !(annR$Do_not_use[m] %in% c("True", "TRUE")))
# one aliquot per patient: the first barcode in sort order
brca <- brca[order(bc[brca])]; dup <- duplicated(patient[brca])
rec("brca_primary_aliquots", sprintf("%d, %d patients (%d repeat aliquots dropped)", length(brca), sum(!dup), sum(dup)))
brca <- brca[!dup]
X <- t(as.matrix(dt[, brca + 1L, with = FALSE])); rm(dt); invisible(gc())
dimnames(X) <- list(bc[brca], genes)
if (!is.double(X)) die("matrix did not parse as numbers")
nas <- colSums(is.na(X))
X <- X[, nas == 0, drop = FALSE]
X[X < 0] <- 0
X <- log2(X + 1)
med <- apply(X, 2, median)
X <- X[, med >= 3, drop = FALSE]
rec("genes", sprintf("%d kept: %d had a missing value, %d below median log2(RSEM+1) 3", ncol(X), sum(nas > 0), sum(med < 3)))
gsg <- goodSamplesGenes(X, verbose = 0)
check("goodSamplesGenes", gsg$allOK, "allOK")
n <- nrow(X); N <- ncol(X)
rec("samples_genes", c(n, N))
lap("read")

# ------------------------------------------------------------------------------------------------
# 1. cor and the signed adjacency against closed forms: every entry against Pearson through BLAS
#    (crossprod of the standardized matrix), and 200 seeded rows against stats::cor, an independent
#    implementation. stats::cor is not run on the whole matrix: it took 6,579 s for the full
#    15k x 15k on Graviton4 (a sizing run), where WGCNA::cor takes seconds.
# ------------------------------------------------------------------------------------------------
BETA <- 12
Cw <- WGCNA::cor(X)
lap("wgcna_cor")
Z <- scale(X); Cb <- crossprod(Z) / (n - 1); rm(Z)
d <- max(abs(Cw - Cb))
check("cor_vs_blas_closed_form", le(d, 1e-12), c("max|diff| over all", N, "x", N, fe(d), "(assert <= 1e-12)"))
A <- adjacency(X, type = "signed", power = BETA)
d <- max(abs(A - ((1 + Cb) / 2)^BETA))
check("adjacency_signed", le(d, 1e-12), c("max|A - ((1+cor)/2)^12| over all, cor through BLAS", fe(d), "(assert <= 1e-12)"))
rm(Cb); invisible(gc())
set.seed(20261017); crow <- sort(sample.int(N, min(200L, N)))
t1 <- proc.time()[["elapsed"]]
Cs <- stats::cor(X[, crow, drop = FALSE], X)
rec("time_stats_cor_200_rows", sprintf("%.1f s", proc.time()[["elapsed"]] - t1))
d <- max(abs(Cw[crow, , drop = FALSE] - Cs))
check("cor_vs_stats_cor_200_rows", le(d, 1e-12), c("max|diff| over 200 x", N, fe(d), "(assert <= 1e-12)"))
rm(Cs); invisible(gc())
rm(Cw); invisible(gc())
lap("cor_checks_adjacency")
rec("peak_rss_cor", hwm())

# ------------------------------------------------------------------------------------------------
# 2. TOM: worked by hand, the closed form on 500 seeded rows of the full TOM, 1 vs N threads
# ------------------------------------------------------------------------------------------------
H <- matrix(c(1, .5, .5, 0,   .5, 1, .5, 0,   .5, .5, 1, 1,   0, 0, 1, 1), 4, 4)
Thand <- matrix(c(1, .5, .5, .25,  .5, 1, .5, .25,  .5, .5, 1, 1,  .25, .25, 1, 1), 4, 4)
dh <- max(abs(TOMsimilarity(H, TOMType = "unsigned", verbose = 0) - Thand))
check("tom_hand_4x4", le(dh, 1e-15), c("max|diff|", fe(dh), "(assert <= 1e-15)"))
TOM <- TOMsimilarity(A, TOMType = "signed", verbose = 0)
lap("tom")
set.seed(20261018); rows <- sort(sample.int(N, min(500L, N)))
A0 <- A; diag(A0) <- 0; k <- rowSums(A0)
L <- A0[rows, , drop = FALSE] %*% A0
Tc <- (L + A0[rows, , drop = FALSE]) / (outer(k[rows], k, pmin) + 1 - A0[rows, , drop = FALSE])
Tc[cbind(seq_along(rows), rows)] <- 1
d <- max(abs(TOM[rows, , drop = FALSE] - Tc))
rm(A0, L, Tc); invisible(gc())
check("tom_closed_form_500_rows", le(d, 1e-12), c("max|diff|", fe(d), sprintf("over 500 x %d entries (assert <= 1e-12)", N)))
check("tom_symmetric", isSymmetric(unclass(TOM), tol = 0), "exact")
rec("tom_sum", sprintf("%.15e", sum(TOM)))
disableWGCNAThreads(); TOM1 <- TOMsimilarity(A, TOMType = "signed", verbose = 0); enableWGCNAThreads(nThreads = NT)
same <- identical(unclass(TOM), unclass(TOM1))
check("tom_1_vs_N_threads", same, sprintf("identical at 1 and %d threads: %s; max|diff| %s", NT, same, fe(max(abs(TOM - TOM1)))))
rm(TOM, TOM1); invisible(gc())
lap("tom_checks")
rec("peak_rss_tom", hwm())

# ------------------------------------------------------------------------------------------------
# 3. the soft-threshold curve (reported; the power is the FAQ's, see the header)
# ------------------------------------------------------------------------------------------------
pw <- c(1:10, seq(12, 20, 2))
sft <- pickSoftThreshold(X, powerVector = pw, networkType = "signed", verbose = 0)
fi <- sft$fitIndices
r2 <- -sign(fi$slope) * fi$SFT.R.sq
rec("pickSoftThreshold_powerEstimate", as.character(sft$powerEstimate))
rec("pickSoftThreshold_signedR2", paste(sprintf("%d:%.3f", pw, r2), collapse = " "))
rec("pickSoftThreshold_meanK", paste(sprintf("%d:%.1f", pw, fi$mean.k.), collapse = " "))
fwrite(fi, file.path(OUT, "sft.tsv"), sep = "\t")
lap("pickSoftThreshold")

# ------------------------------------------------------------------------------------------------
# 4. modules: one block, twice
# ------------------------------------------------------------------------------------------------
bwm <- function(M, block) blockwiseModules(M, power = BETA, networkType = "signed", TOMType = "signed",
  minModuleSize = 30, maxBlockSize = block, mergeCutHeight = 0.25, deepSplit = 2,
  numericLabels = TRUE, pamRespectsDendro = FALSE, randomSeed = 12345, saveTOMs = FALSE, verbose = 0)
net <- bwm(X, N + 1)
lap("modules")
rec("peak_rss_modules", hwm())
net2 <- bwm(X, N + 1)
check("bwm_two_runs_identical", identical(net$colors, net2$colors) && identical(net$MEs, net2$MEs),
      sprintf("colors %s, MEs %s", identical(net$colors, net2$colors), identical(net$MEs, net2$MEs)))
rm(net2); invisible(gc())
lap("modules_rerun")
cl <- net$colors
rec("modules", sprintf("%d non-grey; sizes %s; grey %d of %d genes", nonGrey(cl), sizes(cl), sum(cl == 0), N))
check("modules_found", nonGrey(cl) >= 2, sprintf("%d non-grey (assert >= 2)", nonGrey(cl)))

# eigengene == PC1 of the scaled module
ME <- moduleEigengenes(X, cl)$eigengenes
dmax <- 0
for (mm in sort(setdiff(unique(cl), 0))) {
  Z <- scale(X[, cl == mm])
  pc <- svd(Z, nu = 1, nv = 0)$u[, 1]
  me <- ME[[paste0("ME", mm)]]
  pc <- pc * sign(sum(pc * me))
  dmax <- max(dmax, max(abs(as.numeric(scale(me)) - as.numeric(scale(pc)))))
}
check("ME_eq_first_PC", le(dmax, 1e-12), c("max|scale(ME) - scale(PC1)| over", nonGrey(cl), "modules", fe(dmax), "(assert <= 1e-12)"))
check("bwm_MEs_eq_moduleEigengenes", le(max(abs(as.matrix(net$MEs[, names(ME)]) - as.matrix(ME))), 1e-12),
      fe(max(abs(as.matrix(net$MEs[, names(ME)]) - as.matrix(ME)))))

# kWithin against its definition, from the same adjacency
ic <- intramodularConnectivity(A, cl, scaleByMax = FALSE)
kw <- numeric(N)
for (mm in unique(cl)) { j <- which(cl == mm); kw[j] <- rowSums(A[j, j, drop = FALSE]) - 1 }
d <- max(abs(ic$kWithin - kw) / pmax(kw, 1))
check("kWithin_definition", le(d, 1e-12), c("max rel|diff|", fe(d), "(assert <= 1e-12)"))
hub <- sapply(sort(setdiff(unique(cl), 0)), function(mm) { j <- which(cl == mm); colnames(X)[j[which.max(kw[j])]] })
rec("hub_genes", paste(sprintf("%d:%s", sort(setdiff(unique(cl), 0)), sub("\\|.*", "", hub)), collapse = " "))
rm(A); invisible(gc())
lap("module_checks")

# ------------------------------------------------------------------------------------------------
# 5. published truth: the breast-tumour intrinsic gene clusters (Perou 2000, Sorlie 2001) are
#    co-expression modules -- proliferation, luminal/ER, T cell. Each set's genes must share one
#    non-grey module (>= 80% of the set's kept genes), and the three modules must be distinct.
# ------------------------------------------------------------------------------------------------
sets <- list(
  proliferation = c("MKI67", "TOP2A", "CCNB1", "BUB1", "AURKA", "CDK1", "CENPF", "KIF2C", "MELK", "PTTG1"),
  luminal = c("ESR1", "GATA3", "FOXA1", "XBP1", "TFF1", "TFF3", "SPDEF", "CA12", "MLPH", "AGR3"),
  t_cell = c("CD2", "CD3D", "CD3E", "CD247", "LCK", "ZAP70", "CD8A", "GZMK", "SH2D1A", "ITK"))
sym <- sub("\\|.*", "", colnames(X))
home <- integer()
for (s in names(sets)) {
  j <- which(sym %in% sets[[s]])
  if (anyDuplicated(sym[j])) die(sprintf("a %s marker symbol maps to two genes", s))
  tab <- table(cl[j]); top <- as.integer(names(tab)[which.max(tab)]); frac <- max(tab) / length(j)
  home[s] <- top
  check(paste0("markers_", s), top != 0 && length(j) >= 8 && frac >= 0.8,
        sprintf("%d of %d kept markers in module %d (assert non-grey, >= 80%%): %s", max(tab), length(j), top,
                paste(sprintf("%s=%d", sym[j], cl[j]), collapse = " ")))
}
check("markers_three_distinct_modules", !anyDuplicated(home), paste(names(home), home, sep = "=", collapse = " "))
esr1 <- X[, which(sym == "ESR1")]
rec("cor_ESR1_vs_luminal_ME", sprintf("%.4f", cor(esr1, ME[[paste0("ME", home[["luminal"]])]])))

# ------------------------------------------------------------------------------------------------
# 6. the default a user gets: maxBlockSize = 5000 (pre-clustered blocks) against one block -- reported
# ------------------------------------------------------------------------------------------------
nb <- bwm(X, 5000)
rec("blocked_5000", sprintf("%d blocks, %d non-grey modules, sizes %s; ARI vs one block %.4f; same-grey %d of %d",
    length(nb$blockGenes), nonGrey(nb$colors), sizes(nb$colors), ari(cl, nb$colors), sum((cl == 0) == (nb$colors == 0)), N))
lap("blocked_5000")

# ------------------------------------------------------------------------------------------------
# 7. control: every gene permuted independently across samples -> no co-expression to find
# ------------------------------------------------------------------------------------------------
set.seed(20261019)
XP <- apply(X, 2, function(v) v[sample.int(n)]); dimnames(XP) <- dimnames(X)
np <- bwm(XP, N + 1)
ac <- ari(cl, np$colors)
check("control_permuted", is.finite(ac) && abs(ac) <= 0.05,
      sprintf("ARI vs real modules %.4f (assert |ARI| <= 0.05); %d non-grey modules, %d of %d genes grey", ac, nonGrey(np$colors), sum(np$colors == 0), N))
lap("control")
rec("peak_rss", hwm())

fwrite(data.table(gene = colnames(X), module = cl, blocked_5000 = nb$colors, control = np$colors, kWithin = sprintf("%.17g", kw)),
       file.path(OUT, "modules.tsv"), sep = "\t")
fwrite(data.table(sample = rownames(X), as.data.table(lapply(net$MEs, function(v) sprintf("%.17g", v)))),
       file.path(OUT, "eigengenes.tsv"), sep = "\t")
rec("arch", sprintf("%s (R_ARCH %s)", Sys.info()[["machine"]], R.version$arch))
if (length(fails)) die(paste("checks failed:", paste(fails, collapse = ", ")))
say("WGCNA REAL OK")
