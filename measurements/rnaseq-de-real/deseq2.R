# rnaseq-de-real, task 1 (DESeq2 image). Two objects from the pinned airway 1.30.0 tarball:
#
#   gse    -- tximeta/salmon gene counts, 58,294 genes x 8. Run EXACTLY as the rnaseqGene 1.34.0
#             workflow (Bioconductor 3.22, built with DESeq2 1.50.0 + airway 1.30.0) runs it, and
#             compared against the numbers that workflow publishes.
#   airway -- summarizeOverlaps integer counts, 63,677 genes x 8, no length offsets. Size factors
#             against an independent median-of-ratios recompute, then the DE table task 2 compares
#             limma-voom against. Integer counts with no offsets is what makes that like-with-like.
#
# Every line that only describes the run goes through rec(), which str()s its values, so a
# report line cannot raise and throw away a finished computation.
t_start <- Sys.time()
rec <- function(...) { v <- vapply(list(...), function(x) paste(as.character(x), collapse = ","), ""); line <- paste(v, collapse = "\t"); cat(line, "\n"); cat(line, "\n", file = "report.tsv", append = TRUE) }
fail <- character(0)
check <- function(name, ok, detail) { ok <- isTRUE(ok); rec("CHECK", name, if (ok) "PASS" else "FAIL", detail); if (!ok) fail <<- c(fail, name) }

suppressMessages({ library(DESeq2); library(SummarizedExperiment) })
rec("version", "DESeq2", as.character(packageVersion("DESeq2")))
rec("version", "R", R.version.string)
rec("arch", Sys.info()[["machine"]])
blas <- tryCatch(extSoftVersion()[["BLAS"]], error = function(e) "unknown")
rec("blas", blas)

untar("airway_1.30.0.tar.gz", files = c("airway/data/gse.rda", "airway/data/airway.RData"), tar = "internal")
load("airway/data/gse.rda"); load("airway/data/airway.RData")

# ============================================================== A. the published workflow (gse)
# Verbatim from rnaseqGene 1.34.0, sections 3-5 (magrittr's %<>% written out, as the workflow
# itself says is equivalent).
t0 <- Sys.time()
gse$cell <- gse$donor
gse$dex <- gse$condition
levels(gse$dex) <- c("untrt", "trt")
gse$dex <- relevel(gse$dex, "untrt")
dds <- DESeqDataSet(gse, design = ~ cell + dex)
n_all <- nrow(dds)
smallestGroupSize <- 4
keep <- rowSums(counts(dds) >= 10) >= smallestGroupSize
dds <- dds[keep, ]
dds <- DESeq(dds)
res <- results(dds)
res.05 <- results(dds, alpha = 0.05)
resLFC1 <- results(dds, lfcThreshold = 1)
t_gse <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
rec("time_s", "deseq2_gse", sprintf("%.2f", t_gse))

up <- sum(res$padj < 0.1 & res$log2FoldChange > 0, na.rm = TRUE)
dn <- sum(res$padj < 0.1 & res$log2FoldChange < 0, na.rm = TRUE)
lowc <- sum(!is.na(res$pvalue) & is.na(res$padj))
outl <- sum(is.na(res$pvalue) & res$baseMean > 0)
pub <- c(n_all = 58294, n_kept = 16637, sig_0.1 = 4381, up = 2362, down = 2019, low_counts = 646,
         outliers = 0, sig_alpha_0.05 = 3602, sig_lfc1 = 240)
obs <- c(n_all = n_all, n_kept = nrow(dds), sig_0.1 = sum(res$padj < 0.1, na.rm = TRUE), up = up, down = dn,
         low_counts = lowc, outliers = outl, sig_alpha_0.05 = sum(res.05$padj < 0.05, na.rm = TRUE),
         sig_lfc1 = sum(resLFC1$padj < 0.1, na.rm = TRUE))
for (k in names(pub)) rec("published_count", k, obs[[k]], pub[[k]])
check("published_counts", all(obs == pub), paste(sprintf("%s=%d/%d", names(pub), obs, pub), collapse = " "))

# The ten rows the workflow prints from `res` (head and tail), every printed digit. A printed
# value p with d decimals in its mantissa is matched when |obs - p| <= 0.5e-d * 10^exp: the
# computed value rounds to what the workflow printed. That is the whole precision published.
P <- read.delim(text = "gene\tbaseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj
ENSG00000000003.14\t740.1093\t-0.365327\t0.1073385\t-3.403501\t6.65282e-04\t4.63552e-03
ENSG00000000419.12\t511.6990\t0.202232\t0.1278579\t1.581694\t1.13720e-01\t2.88125e-01
ENSG00000000457.13\t314.1680\t0.033792\t0.1552106\t0.217717\t8.27650e-01\t9.21718e-01
ENSG00000000460.16\t79.7988\t-0.120633\t0.3055270\t-0.394836\t6.92964e-01\t8.48743e-01
ENSG00000000971.15\t5715.3064\t0.442982\t0.0904089\t4.899766\t9.59508e-07\t1.36387e-05
ENSG00000285953.1\t29.5747\t-1.920562\t0.649661\t-2.956253\t0.00311401\t0.0171889
ENSG00000285967.1\t181.1650\t-0.325885\t0.179340\t-1.817132\t0.06919688\t0.2019448
ENSG00000285976.1\t875.4424\t0.262132\t0.142980\t1.833351\t0.06675037\t0.1965808
ENSG00000285979.1\t38.3502\t0.338383\t0.348445\t0.971124\t0.33148631\t0.5713913
ENSG00000285991.1\t11.2772\t-0.115472\t0.723139\t-0.159681\t0.87313221\tNA", colClasses = "character", na.strings = character(0))
halfulp <- function(s) { m <- sub("[eE].*$", "", s); e <- if (grepl("[eE]", s)) as.numeric(sub("^.*[eE]", "", s)) else 0
  d <- if (grepl("\\.", m)) nchar(sub("^.*\\.", "", m)) else 0; 0.5 * 10^(e - d) }
nv <- 0; nbad <- 0
for (i in seq_len(nrow(P))) for (col in names(P)[-1]) {
  s <- P[i, col]; o <- res[P$gene[i], col]
  ok <- if (s == "NA") is.na(o) else (!is.na(o) && abs(o - as.numeric(s)) <= halfulp(s) * (1 + 1e-9))
  nv <- nv + 1; if (!ok) { nbad <- nbad + 1; rec("printed_mismatch", P$gene[i], col, sprintf("%.10g", o), s) }
}
check("published_printed_values", nbad == 0, sprintf("%d of %d printed values match to every printed digit", nv - nbad, nv))
write.table(data.frame(gene = rownames(res), baseMean = sprintf("%.17g", res$baseMean), lfc = sprintf("%.17g", res$log2FoldChange),
                       pvalue = sprintf("%.17g", res$pvalue), padj = sprintf("%.17g", res$padj)),
            "res_deseq2_gse.tsv", sep = "\t", quote = FALSE, row.names = FALSE)

# ============================================================== B. airway: size factors + DE
t0 <- Sys.time()
airway$dex <- relevel(airway$dex, "untrt")
K <- assay(airway, "counts"); storage.mode(K) <- "integer"
keep <- rowSums(K >= 10) >= 4
K <- K[keep, ]
cd <- data.frame(sample = colnames(K), cell = factor(airway$cell), dex = airway$dex)
da <- DESeqDataSetFromMatrix(K, cd, design = ~ cell + dex)
da <- estimateSizeFactors(da)
sf <- sizeFactors(da)
# Independent recompute of Anders & Huber (2010) eq. 5 in RATIO space, with no logs: the
# geometric mean as prod()^(1/m) (8 samples of counts <= ~1e6 cannot overflow a double) and the
# median of k_ij / gm_i. With an even gene count the median sits between two middle ratios, and
# the convention matters: DESeq2 takes the median in log space, which is their GEOMETRIC mean
# sqrt(lo*hi); the textbook median is their arithmetic mean. The assertion uses the geometric
# one, so it is exact to rounding (64 eps relative); the arithmetic one is reported, and its gap
# is bounded by AM-GM, (hi - lo)^2 / (8 lo) -- a convention, not an error.
pos <- rowSums(K == 0) == 0
Kp <- K[pos, , drop = FALSE]
gm <- apply(Kp, 1, function(x) prod(as.numeric(x))^(1 / length(x)))
sfg <- sfa <- amgm <- numeric(ncol(Kp))
for (j in seq_len(ncol(Kp))) {
  r <- sort(Kp[, j] / gm); n <- length(r)
  if (n %% 2 == 1) { sfg[j] <- sfa[j] <- r[(n + 1) / 2] } else {
    lo <- r[n / 2]; hi <- r[n / 2 + 1]; sfg[j] <- sqrt(lo * hi); sfa[j] <- (lo + hi) / 2; amgm[j] <- (hi - lo)^2 / (8 * lo) }
}
rel <- abs(sf - sfg) / sf
gap <- abs(sf - sfa)
rec("sizefactor_genes_used", sum(pos), if (sum(pos) %% 2 == 0) "even" else "odd")
rec("sizefactor_cols", "sample", "deseq2", "independent_geometric", "rel_diff", "arithmetic_median_gap", "amgm_bound")
for (j in seq_along(sf)) rec("sizefactor", names(sf)[j], sprintf("%.17g", sf[j]), sprintf("%.17g", sfg[j]), sprintf("%.3e", rel[j]), sprintf("%.3e", gap[j]), sprintf("%.3e", amgm[j]))
check("sizefactor_recompute", all(rel <= 64 * .Machine$double.eps), sprintf("max relative |deseq2 - independent| = %.3e (assert <= 64 eps = %.3e)", max(rel), 64 * .Machine$double.eps))
check("sizefactor_amgm_gap", all(gap <= amgm * (1 + 1e-6) + 64 * .Machine$double.eps), sprintf("arithmetic-median convention gap max %.3e, within AM-GM bound", max(gap)))

da <- DESeq(da, quiet = TRUE)
ra <- results(da)
t_air <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
rec("time_s", "deseq2_airway", sprintf("%.2f", t_air))
rec("airway_genes", nrow(airway), "kept", nrow(K))
rec("airway_sig_0.05", sum(ra$padj < 0.05, na.rm = TRUE))

write.table(data.frame(gene = rownames(K), K, check.names = FALSE), "airway_counts.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
write.table(cd, "airway_coldata.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
write.table(data.frame(gene = rownames(ra), baseMean = sprintf("%.17g", ra$baseMean), lfc = sprintf("%.17g", ra$log2FoldChange),
                       pvalue = sprintf("%.17g", ra$pvalue), padj = sprintf("%.17g", ra$padj)),
            "res_deseq2_airway.tsv", sep = "\t", quote = FALSE, row.names = FALSE)

hwm <- tryCatch(sub("^VmHWM:\\s*", "", grep("^VmHWM", readLines("/proc/self/status"), value = TRUE)), error = function(e) "unknown")
rec("peak_rss", hwm)
rec("time_s", "rscript_total", sprintf("%.2f", as.numeric(difftime(Sys.time(), t_start, units = "secs"))))
if (length(fail)) { rec("RESULT", "FAIL", paste(fail, collapse = ",")); quit(status = 1) }
rec("RESULT", "PASS", "deseq2")
