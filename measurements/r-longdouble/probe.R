# Why is stats::cor slow on arm64 Linux? Facts, static evidence, timings and agreement, on one box.
#   Rscript probe.R <out dir> <threads>
args <- commandArgs(TRUE); O <- args[1]; NT <- as.integer(args[2])
rec <- function(k, v) { cat(sprintf("%-34s %s\n", k, paste(format(v), collapse = " ")), file = file.path(O, "results.tsv"), append = TRUE); cat(k, paste(format(v), collapse = " "), "\n") }
tm <- function(expr) { t <- system.time(expr)[["elapsed"]]; t }

# 1. what long double IS here
rec("R", R.version.string)
rec("sizeof_longdouble", .Machine$sizeof.longdouble)
rec("longdouble_digits", .Machine$longdouble.digits)   # 64 = x87 extended, 113 = IEEE quad
rec("capabilities_long_double", capabilities("long.double"))
rec("blas", extSoftVersion()[["BLAS"]])
rec("lapack", La_library())

# 2. static: does stats.so reference the libgcc soft-float quad routines?
so <- file.path(R.home("library"), "stats", "libs", "stats.so")
raw <- readBin(so, "raw", file.info(so)$size); txt <- rawToChar(raw[raw != as.raw(0)])
for (s in c("__addtf3", "__multf3", "__divtf3", "__subtf3", "__extenddftf2", "__trunctfdf2"))
  rec(paste0("stats.so_refs_", s), grepl(s, txt, fixed = TRUE))
rm(raw, txt)

# 3. timings on one seeded matrix, the wgcna-real shape: 1,083 samples x 15,045 features
n <- 1083L; p <- 15045L
set.seed(20261018); X <- matrix(rnorm(n * p), n, p)
rec("matrix", c(n, p))
Xs <- X[, 1:3000]
rec("t_stats_cor_3000", tm(S3 <- stats::cor(Xs)))
rec("t_crossprod_3000", tm(B3 <- crossprod(scale(Xs)) / (n - 1)))
rec("maxdiff_stats_vs_crossprod_3000", max(abs(S3 - B3)))
suppressMessages(library(WGCNA)); allowWGCNAThreads(NT)
rec("t_wgcna_cor_3000", tm(W3 <- WGCNA::cor(Xs)))
rec("maxdiff_stats_vs_wgcna_3000", max(abs(S3 - W3)))
rec("identical_stats_vs_crossprod_3000", identical(S3, B3))
rm(S3, B3, W3); gc()

# full matrix
rec("t_crossprod_full", tm(B <- crossprod(scale(X)) / (n - 1)))
rec("t_wgcna_cor_full", tm(W <- WGCNA::cor(X)))
rec("maxdiff_crossprod_vs_wgcna_full", max(abs(B - W))); rm(W); gc()
rec("t_stats_cor_full", tm(S <- stats::cor(X)))
rec("maxdiff_stats_vs_crossprod_full", max(abs(S - B)))
rec("sha_free_digest_stats_full", sprintf("%.17g", sum(S * seq_len(p)[col(S)])))   # cross-chip comparison value
rm(S, B); gc()

# 4. other LDOUBLE accumulators vs a double-accumulating BLAS baseline on the same vector
x <- rnorm(2e8)
rec("t_sum_x10", tm(for (i in 1:10) sum(x)))
rec("t_mean_x10", tm(for (i in 1:10) mean(x)))
rec("t_blas_ddot_x10", tm(for (i in 1:10) crossprod(x, rep_len(1, length(x)))))
M <- matrix(x, ncol = 1e4); rm(x); gc()
rec("t_colSums_x10", tm(for (i in 1:10) colSums(M)))
rm(M); gc()

# 5. missing data: WGCNA::cor vs stats::cor(use = "pairwise.complete.obs")
set.seed(7); Y <- matrix(rnorm(500 * 200), 500, 200); Y[sample(length(Y), 0.1 * length(Y))] <- NA
P <- stats::cor(Y, use = "pairwise.complete.obs")
for (u in c("all.obs", "pairwise.complete.obs")) {
  r <- tryCatch(WGCNA::cor(Y, use = u), error = function(e) conditionMessage(e))
  rec(paste0("wgcna_use_", u), if (is.character(r)) paste("error:", r) else sprintf("maxdiff_vs_stats_pairwise %.3g  NAs %d", max(abs(r - P), na.rm = TRUE), sum(is.na(r))))
}
rec("wgcna_default_use", deparse(formals(WGCNA::cor)$use))

# 6. are the packaged alternatives installed in this image?
for (pk in c("coop", "Rfast")) rec(paste0("installed_", pk), requireNamespace(pk, quietly = TRUE))
rec("PROBE", "OK")
