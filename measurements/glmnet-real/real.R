# glmnet on the whole TCGA PanCanAtlas RNA-seq cohort, held to its own optimality conditions.
#
#   Rscript real.R <data dir> <out dir> <expected genes> <expected samples>
#
# Two fits a researcher actually runs on this matrix, each at the size it is run:
#   A. binomial lasso, tumour vs solid-tissue normal, every usable sample of all 33 cancer types
#      (n ~ 11k, p ~ 17k): the whole cohort.
#   B. gaussian lasso, ESR1 expression from every other gene, in the BRCA tumours (n ~ 1.1k,
#      p ~ 17k): one cancer type, p >> n, the classic gene-signature shape.
# p > n in both, so the recipe's lm()/glm() legs do not exist; what is asserted is what holds at any
# size (see recipes/glmnet): lambda_max in closed form, the KKT conditions at every lambda on the
# path with a control that must fail, the intercept score, and (B) ridge against its closed form,
# solved through the n x n dual because the p x p primal is 17k square.
#
# KKT tolerance, as in the recipe: glmnet stops a sweep when no single update changes the objective
# by more than thresh x the null deviance, so the gradient is off by at most p * sqrt(thresh)
# (sqrt(2 thresh) binomial), in units of s_j * sd_y. p is ~17k here, so THRESH is 1e-20 to keep the
# bound at ~1e-6 and the shifted-lambda control 1000x outside it.
options(warn = 1)
args <- commandArgs(trailingOnly = TRUE)
DATA <- args[1]; OUT <- args[2]
WANT_G <- as.integer(args[3]); WANT_S <- as.integer(args[4])
dir.create(OUT, showWarnings = FALSE)
SCORE <- file.path(OUT, "score.tsv"); LOG <- file.path(OUT, "real.log")
cat("observable\tvalue\n", file = SCORE)
# every line goes straight to a file that is opened and closed per write, so a killed run keeps it
say <- function(...) { m <- sprintf(...); cat(m, "\n", sep = ""); cat(m, "\n", sep = "", file = LOG, append = TRUE) }
rec <- function(k, v) {
  v <- paste(vapply(v, function(e) as.character(e), ""), collapse = " ")
  cat(sprintf("%s\t%s\n", k, v), file = SCORE, append = TRUE); say("%s\t%s", k, v)
}
die <- function(msg) {
  if (length(warns)) say("warnings so far: %s", paste(unique(warns), collapse = " | "))
  say("FAIL: %s", msg); quit(status = 1)
}
fe <- function(x) sprintf("%.3e", x)
f17 <- function(x) sprintf("%.17g", x)
# A non-finite value passes every one-sided bound (NaN > tol is FALSE), so every check goes here.
ok <- function(x, tol) isTRUE(is.finite(x) && x <= tol)
T0 <- proc.time()[["elapsed"]]
lap <- function(k) { t <- proc.time()[["elapsed"]]; rec(paste0("time_", k), sprintf("%.1f s", t - T0)); T0 <<- t }
hwm <- function() {   # this R process's peak resident set, from the kernel
  l <- grep("^VmHWM", readLines("/proc/self/status"), value = TRUE)
  sprintf("%.2f GiB", as.numeric(gsub("[^0-9]", "", l)) / 1048576)
}
warns <- character()
quiet_fit <- function(expr, tag) withCallingHandlers(expr, warning = function(w) {
  warns <<- c(warns, paste0(tag, ": ", conditionMessage(w))); invokeRestart("muffleWarning") })
# glmnet does not fail when coordinate descent runs out of passes: it warns and returns the path
# down to the last lambda that converged. So a tight-threshold fit reports how far it got.
unconverged <- function(tag) grep("not reached", warns[startsWith(warns, paste0(tag, ": "))], value = TRUE)

suppressPackageStartupMessages({ library(glmnet); library(data.table) })
rec("r_version", paste0(R.version$major, ".", R.version$minor))
rec("glmnet", as.character(packageVersion("glmnet")))
rec("data.table", as.character(packageVersion("data.table")))
rec("r_arch", R.version$arch)
rec("blas", La_library())

THRESH <- 1e-20
MAXIT <- 1e6    # passes over the data, all lambdas together; the default 1e5 is not enough at THRESH
EXACT <- 1e-30
EPS <- .Machine$double.eps
popsd <- function(x) sqrt(mean((x - mean(x))^2))
colpopsd <- function(X) { m <- colMeans(X); sqrt(pmax(colMeans(X^2) - m^2, 0)) }

# ------------------------------------------------------------------------------------------------
# The matrix: genes x samples, "SYMBOL|ENTREZ" row names, EB++ batch-adjusted RSEM values.
# ------------------------------------------------------------------------------------------------
dt <- fread(file.path(DATA, "EBPlusPlusAdjustPANCAN_IlluminaHiSeq_RNASeqV2.geneExp.tsv"),
            sep = "\t", header = TRUE, na.strings = c("NA", ""), showProgress = FALSE)
genes <- dt[[1]]; bc <- names(dt)[-1]
dt[, (1L) := NULL]
E <- t(as.matrix(dt)); rm(dt); invisible(gc())
dimnames(E) <- list(bc, genes)
lap("read")
rec("matrix_genes_samples", c(ncol(E), nrow(E)))
if (WANT_G > 0 && (ncol(E) != WANT_G || nrow(E) != WANT_S))
  die(sprintf("matrix is %d genes x %d samples, the release is %d x %d", ncol(E), nrow(E), WANT_G, WANT_S))
if (anyDuplicated(genes)) die("duplicate gene ids")
if (anyDuplicated(bc)) die("duplicate sample barcodes")
if (!is.double(E)) die("matrix did not parse as numbers")
rec("matrix_na_cells", sum(is.na(E)))
rec("matrix_negative_cells", sum(E < 0, na.rm = TRUE))

ann <- fread(file.path(DATA, "merged_sample_quality_annotations.tsv"), sep = "\t", header = TRUE,
             colClasses = "character", showProgress = FALSE)
# an aliquot also appears under its miRNA-seq platform, so keep the RNA-seq rows (HiSeq and GA)
# One row per aliquot per platform. Use the RNA-seq row (HiSeq or GA); 20 aliquots were sequenced
# on both and are collapsed only where the two rows agree. 33 OV aliquots carry a row only under
# miRNA-seq (the same aliquot), which is used only where it is that aliquot's single annotation.
key <- c("aliquot_barcode", "cancer type", "Do_not_use")
annR <- unique(ann[platform %in% c("IlluminaHiSeq_RNASeqV2", "IlluminaGA_RNASeqV2"), key, with = FALSE])
annO <- unique(ann[, key, with = FALSE])
if (anyDuplicated(annR$aliquot_barcode)) die("an RNA-seq aliquot carries conflicting annotations")
mR <- match(bc, annR$aliquot_barcode); mO <- match(bc, annO$aliquot_barcode)
rest <- is.na(mR)
if (any(rest & (is.na(mO) | annO$aliquot_barcode[mO] %in% annO$aliquot_barcode[duplicated(annO$aliquot_barcode)])))
  die("a sample in the matrix has no single annotation")
rec("samples_annotated", sprintf("%d of %d (%d from the RNA-seq row, %d from the aliquot's only row)",
                                 sum(!rest) + sum(rest), length(bc), sum(!rest), sum(rest)))
ctype <- ifelse(rest, annO[["cancer type"]][mO], annR[["cancer type"]][mR])
dnu <- ifelse(rest, annO$Do_not_use[mO], annR$Do_not_use[mR]) %in% c("True", "TRUE")
code <- as.integer(substr(bc, 14, 15))
patient <- substr(bc, 1, 12)
rec("cancer_types", length(unique(ctype)))
rec("do_not_use", sum(dnu))

# log2(x + 1); the batch adjustment leaves a few small negative values, floored at 0 first
prep <- function(rows, drop = character()) {
  X <- E[rows, , drop = FALSE]
  nas <- colSums(is.na(X))
  rec("genes_with_missing_values_dropped", sprintf("%d (in %d of %d samples at most)", sum(nas > 0), max(nas), nrow(X)))
  X <- X[, nas == 0 & !(colnames(X) %in% drop), drop = FALSE]
  X[X < 0] <- 0
  X <- log2(X + 1)
  X[, colpopsd(X) > 0, drop = FALSE]
}
# folds by patient: a tumour and its matched normal never sit on opposite sides of a split
folds_by_patient <- function(pt, k, seed) {
  set.seed(seed); u <- unique(pt)
  f <- rep_len(seq_len(k), length(u))[sample.int(length(u))]
  f[match(pt, u)]
}

kkt <- function(X, s, fit, r_of) {
  n <- nrow(X); B <- fit$beta; worst <- 0; worst_inact <- 0
  for (k in seq_along(fit$lambda)) {
    b <- B[, k]; r <- r_of(fit$a0[k], b)
    g <- as.numeric(crossprod(X, r)) / n
    lam <- fit$lambda[k] * s
    act <- b != 0
    v_act <- if (any(act)) max(abs(g[act] - lam[act] * sign(b[act])) / s[act]) else 0
    v_ina <- if (any(!act)) max(pmax(abs(g[!act]) - lam[!act], 0) / s[!act]) else 0
    worst <- max(worst, v_act, v_ina); worst_inact <- max(worst_inact, v_ina)
  }
  c(worst, worst_inact)
}
lambda_max <- function(X, y, s) max(abs(as.numeric(crossprod(X, y - mean(y)))) / s) / nrow(X)
shift <- function(fit) { K <- ncol(fit$beta); sh <- c(2:K, K)
  off <- fit; off$beta <- fit$beta[, sh, drop = FALSE]; off$a0 <- fit$a0[sh]; off }
write_path <- function(fit, name) {
  B <- summary(fit$beta)   # i, j, x of the nonzeros
  fwrite(data.table(lambda_index = B$j, gene = rownames(fit$beta)[B$i], coef = f17(B$x)),
         file.path(OUT, paste0(name, "-path.tsv")), sep = "\t")
  fwrite(data.table(lambda_index = seq_along(fit$lambda), lambda = f17(fit$lambda), df = fit$df,
                    dev_ratio = f17(fit$dev.ratio), a0 = f17(as.numeric(fit$a0))),
         file.path(OUT, paste0(name, "-lambda.tsv")), sep = "\t")
}
nzv <- function(b) { b <- b[-1, 1]; names(b)[b != 0] }

# ================================================================================================
# A. binomial lasso: tumour (sample type 01-09) vs solid-tissue normal (11), the whole cohort
# ================================================================================================
ra <- which(!dnu & (code < 10 | code == 11))
XA <- prep(ra); yA <- as.integer(code[ra] < 10); pA <- patient[ra]
nA <- nrow(XA); P <- ncol(XA)
rec("A_n_p", c(nA, P))
rec("A_tumour_normal", c(sum(yA), sum(1 - yA)))
rec("A_patients", length(unique(pA)))
sA <- colpopsd(XA)
lap("A_prep")

fA <- quiet_fit(glmnet(XA, yA, family = "binomial", alpha = 1, control = list(thresh = THRESH, maxit = MAXIT)), "A_fit")
lap("A_fit")
fAd <- quiet_fit(glmnet(XA, yA, family = "binomial", alpha = 1), "A_default")
rec("A_path_length", sprintf("%d (default thresh: %d)", length(fA$lambda), length(fAd$lambda)))
rec("A_unconverged_reported", if (length(unconverged("A_fit"))) unconverged("A_fit") else "none")
rec("A_max_df", max(fA$df))
rec("A_final_dev_ratio", sprintf("%.6f", fA$dev.ratio[length(fA$lambda)]))
write_path(fA, "A")
lmA <- lambda_max(XA, yA, sA)
d <- abs(fA$lambda[1] - lmA) / lmA
rec("A_lambda_max", sprintf("glmnet %s closed form %s rel %s", f17(fA$lambda[1]), f17(lmA), fe(d)))
if (!ok(d, 1e-12)) die(sprintf("binomial lambda_max off by %s relative", fe(d)))
if (sum(fA$beta[, 1] != 0) != 0) die("binomial coefficients nonzero at lambda_max")
binomA <- function(a0, b) yA - 1 / (1 + exp(-(a0 + as.numeric(XA %*% b))))
kA <- kkt(XA, sA, fA, binomA)
bA <- P * sqrt(2 * THRESH)
rec("A_kkt_max_violation", sprintf("%s (inactive %s) bound %s", fe(kA[1]), fe(kA[2]), fe(bA)))
if (!ok(kA[1], bA)) die(sprintf("binomial lasso KKT violated: %s > %s", fe(kA[1]), fe(bA)))
koA <- kkt(XA, sA, shift(fA), binomA)[1]
rec("A_kkt_control_shifted_lambda", sprintf("%s (%.0fx the bound)", fe(koA), koA / bA))
if (!is.finite(koA) || koA < 1000 * bA) die("binomial KKT check does not discriminate a wrong solution")
iA <- max(abs(vapply(seq_along(fA$lambda), function(k) mean(binomA(fA$a0[k], fA$beta[, k])), 0)))
rec("A_intercept_score", sprintf("max |mean(y - p)| %s", fe(iA)))
if (!ok(iA, bA)) die("binomial intercept score equation violated")
lap("A_checks")

foldA <- folds_by_patient(pA, 10, 20261018)
cvA <- quiet_fit(cv.glmnet(XA, yA, family = "binomial", foldid = foldA, alpha = 1, type.measure = "class"), "A_cv")
lap("A_cv")
selA <- nzv(coef(cvA, s = "lambda.1se"))
rec("A_cv_lambda_1se", f17(cvA$lambda.1se))
if (cvA$lambda.1se < min(fA$lambda)) die("lambda.1se lies below the KKT-certified path")
rec("A_cv_misclass_1se_reported", sprintf("%.5f", cvA$cvm[cvA$index["1se", 1]]))
rec("A_cv_selected_1se_reported", sprintf("%d: %s", length(selA), paste(sub("\\|.*", "", selA), collapse = ",")))
fwrite(data.table(lambda = f17(cvA$lambda), cvm = f17(cvA$cvm), cvsd = f17(cvA$cvsd)),
       file.path(OUT, "A-cv.tsv"), sep = "\t")
rec("A_peak_rss", hwm())
rm(XA); invisible(gc())

# ================================================================================================
# B. gaussian lasso: ESR1 from every other gene, BRCA tumours
# ================================================================================================
ESR1 <- "ESR1|2099"
if (sum(genes == ESR1) != 1) die("ESR1|2099 is not exactly one row")
rb <- which(!dnu & ctype == "BRCA" & code < 10)
if (anyNA(E[rb, ESR1])) die("ESR1 has missing values in BRCA")
y <- log2(pmax(E[rb, ESR1], 0) + 1)
X <- prep(rb, drop = ESR1); pB <- patient[rb]
n <- nrow(X); p <- ncol(X)
rec("B_n_p", c(n, p))
rec("B_patients", length(unique(pB)))
s <- colpopsd(X); sdy <- popsd(y)
Xc <- sweep(X, 2, colMeans(X)); Xs <- sweep(Xc, 2, s, "/"); rm(Xc)
lap("B_prep")

fit <- quiet_fit(glmnet(X, y, family = "gaussian", alpha = 1, control = list(thresh = THRESH, maxit = MAXIT)), "B_fit")
lap("B_fit")
rec("B_unconverged_reported", if (length(unconverged("B_fit"))) unconverged("B_fit") else "none")
rec("B_max_df", max(fit$df))
write_path(fit, "B")
lmax <- lambda_max(X, y, s)
d <- abs(fit$lambda[1] - lmax) / lmax
rec("B_lambda_max", sprintf("glmnet %s closed form %s rel %s", f17(fit$lambda[1]), f17(lmax), fe(d)))
if (!ok(d, 1e-12)) die(sprintf("gaussian lambda_max off by %s relative", fe(d)))
if (sum(fit$beta[, 1] != 0) != 0) die("gaussian coefficients nonzero at lambda_max")
if (!ok(abs(fit$a0[1] - mean(y)) / abs(mean(y)), 1e-12)) die("intercept at lambda_max is not mean(y)")
f5 <- quiet_fit(glmnet(X, y, family = "gaussian", alpha = 0.5, nlambda = 3, control = list(thresh = THRESH, maxit = MAXIT)), "B_alpha_0.5")
d5 <- abs(f5$lambda[1] - lmax / 0.5) / (lmax / 0.5)
rec("B_lambda_max_alpha_0.5", sprintf("rel %s", fe(d5)))
if (!ok(d5, 1e-12)) die("alpha=0.5 lambda_max is not lambda_max(alpha=1)/0.5")

gaussB <- function(a0, b) y - a0 - as.numeric(X %*% b)
bound <- p * sqrt(THRESH)
kv <- kkt(X, s, fit, gaussB) / sdy
rec("B_kkt_max_violation", sprintf("%s (inactive %s) bound %s", fe(kv[1]), fe(kv[2]), fe(bound)))
if (!ok(kv[1], bound)) die(sprintf("gaussian lasso KKT violated: %s > %s", fe(kv[1]), fe(bound)))
ko <- kkt(X, s, shift(fit), gaussB)[1] / sdy
rec("B_kkt_control_shifted_lambda", sprintf("%s (%.0fx the bound)", fe(ko), ko / bound))
if (!is.finite(ko) || ko < 1000 * bound) die("gaussian KKT check does not discriminate a wrong solution")
fd <- quiet_fit(glmnet(X, y, family = "gaussian", alpha = 1), "B_default")
kd <- kkt(X, s, fd, gaussB)[1] / sdy
rec("B_kkt_default_thresh_reported", fe(kd))
rec("B_path_length", sprintf("%d (default thresh: %d)", length(fit$lambda), length(fd$lambda)))
lap("B_checks")

# ridge == closed form, through the dual: (X'X/n + lambda I)^-1 X'y/n == X'(XX'/n + lambda I)^-1 y/n
ys <- (y - mean(y)) / sdy
K <- tcrossprod(Xs) / n
mu <- eigen(K, symmetric = TRUE, only.values = TRUE)$values
lam_r <- 10^seq(3, 1, length.out = 5)
kap <- (max(mu) + min(lam_r)) / (max(min(mu), 0) + min(lam_r))
xtol <- 1e3 * kap * EPS
rec("B_dual_gram_top_eigenvalue", sprintf("%.3f", max(mu)))
fr <- quiet_fit(glmnet(Xs, ys, family = "gaussian", alpha = 0, lambda = lam_r, standardize = FALSE,
                       intercept = FALSE, control = list(thresh = EXACT)), "B_ridge")
dual <- function(l, v) as.numeric(crossprod(Xs, solve(K + l * diag(n), v))) / n
rd <- max(vapply(seq_along(lam_r), function(k) max(abs(as.numeric(fr$beta[, k]) - dual(lam_r[k], ys))), 0))
rec("B_ridge_vs_dual_closed_form", sprintf("%s over %d lambdas, kappa %.1f, bound %s", fe(rd), length(lam_r), kap, fe(xtol)))
if (!ok(rd, xtol)) die(sprintf("ridge differs from its closed form by %s", fe(rd)))
lap("B_ridge")
# On the raw y scale the ridge glmnet solves has lambda/sd(y). The lambdas are given as
# lam_r * sd(y), so the penalty it solves, and the conditioning, are the same as above.
lam_y <- lam_r * sdy
fro <- quiet_fit(glmnet(Xs, y, family = "gaussian", alpha = 0, lambda = lam_y, standardize = FALSE,
                        control = list(thresh = EXACT)), "B_ridge_raw_y")
if (ncol(fro$beta) != length(lam_y)) die(sprintf("raw-y ridge path stopped at %d of %d lambdas", ncol(fro$beta), length(lam_y)))
yc <- y - mean(y)
rdev <- function(f) max(vapply(seq_along(lam_y), function(k)
  max(abs(as.numeric(fro$beta[, k]) - dual(f(lam_y[k]), yc))) / sdy, 0))
rdo <- rdev(function(l) l / sdy); rdn <- rdev(function(l) l)
rec("B_ridge_raw_y_lambda_over_sdy", fe(rdo))
rec("B_ridge_raw_y_naive_lambda_reported", fe(rdn))
if (!ok(rdo, xtol)) die("ridge on raw y does not solve with lambda/sd(y)")
rm(K, Xs); invisible(gc())
lap("B_ridge_raw_y")

foldB <- folds_by_patient(pB, 10, 20261018)
cv1 <- quiet_fit(cv.glmnet(X, y, foldid = foldB, alpha = 1), "B_cv")
cv2 <- quiet_fit(cv.glmnet(X, y, foldid = foldB, alpha = 1), "B_cv")
if (!identical(cv1$cvm, cv2$cvm)) die("cv.glmnet with a fixed foldid is not deterministic")
rec("B_cv_deterministic", "identical cvm over two runs")
lap("B_cv")
selB <- nzv(coef(cv1, s = "lambda.1se"))
rec("B_cv_lambda_1se", f17(cv1$lambda.1se))
if (cv1$lambda.1se < min(fit$lambda)) die("lambda.1se lies below the KKT-certified path")
rec("B_cv_r2_1se_reported", sprintf("%.4f", 1 - cv1$cvm[cv1$index["1se", 1]] / mean((y - mean(y))^2)))
rec("B_cv_selected_1se_reported", sprintf("%d: %s", length(selB), paste(sub("\\|.*", "", selB), collapse = ",")))
fwrite(data.table(lambda = f17(cv1$lambda), cvm = f17(cv1$cvm), cvsd = f17(cv1$cvsd)),
       file.path(OUT, "B-cv.tsv"), sep = "\t")
# permuted-y nulls: no real association left, so the 1se model must be empty
set.seed(20261018)
NNULL <- 5L
nsel <- vapply(seq_len(NNULL), function(i) {
  cvn <- quiet_fit(cv.glmnet(X, y[sample.int(n)], foldid = foldB, alpha = 1), "B_null")
  length(nzv(coef(cvn, s = "lambda.1se")))
}, 0L)
rec("B_null_1se_selected", paste(nsel, collapse = ","))
lap("B_null")
if (any(nsel != 0)) die(sprintf("a permuted-y null selected genes at lambda.1se: %s", paste(nsel, collapse = ",")))
rec("peak_rss", hwm())
rec("warnings", if (length(warns)) paste(unique(warns), collapse = " | ") else "none")
cat("GLMNET REAL OK\n", file = LOG, append = TRUE)
cat("GLMNET REAL OK\n")
