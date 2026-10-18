# methylation-array-real, task 2 of 2 (SeSAMe image): the same 689 IDAT pairs through openSesame,
# then two cross-code claims against task 1's minfi results -- who is which sample, and which sex.
#
#   Rscript sesame-real.R    (inputs in /tmp: idat/, pheno.tsv, minfi-raw-betas.rds, sex-minfi.tsv;
#                             HOME holds the pinned ExperimentHub cache; writes /tmp/out/)
suppressMessages({library(sesame); library(BiocParallel)})
O <- "/tmp/out"; dir.create(O, showWarnings = FALSE)
out <- file(file.path(O, "checks.txt"), open = "wt")
say <- function(...) { s <- paste0(...); cat(s, "\n", sep = ""); cat(s, "\n", sep = "", file = out); flush(out) }
fail <- character(0)
ck <- function(k, ok, v) { say(sprintf("%-34s %s %s", k, if (isTRUE(ok)) "ok  " else "FAIL", v)); if (!isTRUE(ok)) fail <<- c(fail, k) }
ob <- function(k, v) say(sprintf("%-34s %s   [observation]", k, v))
T <- file(file.path(O, "times.txt"), open = "wt")
tm <- function(label, expr) { t0 <- Sys.time(); v <- force(expr)
  s <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  cat(sprintf("%-22s %8.1f s\n", label, s)); cat(sprintf("%s\t%.1f\n", label, s), file = T); flush(T); v }
pv <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) "unknown")
for (p in c("sesame", "sesameData", "preprocessCore")) say(sprintf("%-34s %s", paste0(p, "_version"), pv(p)))
NW <- as.integer(Sys.getenv("WORKERS", "16"))
say(sprintf("%-34s %d", "workers", NW))

pd <- read.delim("/tmp/pheno.tsv", colClasses = "character")
ck("samples", nrow(pd) == 689 && length(unique(pd$gsm)) == 689, sprintf("%d rows, %d unique GSM", nrow(pd), length(unique(pd$gsm))))

# Each sample is an independent per-array computation, so workers change the schedule, not the result.
S <- tm("openSesame", openSesame(file.path("/tmp/idat", pd$basename), BPPARAM = MulticoreParam(NW)))
colnames(S) <- pd$gsm
ck("sesame_betas", nrow(S) == 486427 && ncol(S) == 689,
   sprintf("%d x %d, %s non-NA  want 486427 x 689 (HM450 address order)", nrow(S), ncol(S), format(sum(!is.na(S)), big.mark = ",")))
rs <- range(S, na.rm = TRUE)
ck("sesame_beta_in_range", rs[1] >= 0 && rs[2] <= 1, sprintf("[%.6f, %.6f]", rs[1], rs[2]))
ob("sesame_betas_sum", sprintf("%.10f over %d non-NA (a fingerprint for repeat runs)", sum(S, na.rm = TRUE), sum(!is.na(S))))
ob("sesame_nonNA_per_sample", sprintf("min %d  median %d  max %d", min(colSums(!is.na(S))), as.integer(median(colSums(!is.na(S)))), max(colSums(!is.na(S)))))

# SEX, cross-code: SeSAMe's inferSex reads methylation of X-linked probes (betas only); minfi's getSex
# reads total X/Y intensity. Two different signals and two different rules, from the same IDATs.
# serial: under MulticoreParam a worker failed to deliver ("wrong args for environment subassignment")
sx <- tm("inferSex", vapply(seq_len(ncol(S)), function(j) as.character(inferSex(S[, j])), ""))
sx <- c(MALE = "M", FEMALE = "F")[sx]
sm <- read.delim("/tmp/sex-minfi.tsv", colClasses = "character")
stopifnot(identical(sm$gsm, pd$gsm))
both <- sx == sm$minfi
ck("sex_sesame_equals_minfi", all(both), sprintf("%d/%d samples: both codes call the same sex", sum(both), length(both)))
ob("sesame_sex_vs_annotation", sprintf("%d/%d agree", sum(sx == sm$annotated), length(sx)))
mis <- which(sm$minfi != sm$annotated)
ck("annotation_mismatches_confirmed", all(sx[mis] == sm$minfi[mis]),
   sprintf("%d samples whose annotation minfi contradicts; SeSAMe agrees with minfi on %d of them", length(mis), sum(sx[mis] == sm$minfi[mis])))
for (i in mis) ob("  sex_mismatch", sprintf("%s annotated %s, minfi %s, sesame %s", sm$gsm[i], sm$annotated[i], sm$minfi[i], sx[i]))
write.table(data.frame(sm, sesame = sx), file.path(O, "sex.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)

# IDENTITY, cross-code: the recipe's matching, 689-way. Rank correlation (the two pipelines normalise
# differently, so raw betas are not comparable), probes ranked by variance in minfi's matrix alone.
M <- tm("read_minfi", readRDS("/tmp/minfi-raw-betas.rds"))
ck("minfi_betas_read", nrow(M) == 485512 && ncol(M) == 689 && identical(colnames(M), pd$gsm),
   sprintf("%d x %d, columns in pheno order: %s", nrow(M), ncol(M), identical(colnames(M), pd$gsm)))
shared <- intersect(rownames(M), rownames(S))
Ms <- M[shared, ]; Ss <- S[shared, colnames(Ms)]
keep <- rowSums(is.na(Ss)) == 0 & rowSums(is.na(Ms)) == 0
Ms <- Ms[keep, ]; Ss <- Ss[keep, ]
ob("probes_both_retain_all_689", sprintf("%d of %d shared", nrow(Ms), length(shared)))
rm(M)
match_at <- function(Ma, Sa) {
  # Spearman is Pearson on (average) ranks; as a cross-product of unit-norm centred rank columns it
  # is the same number as cor(method = "spearman") at BLAS speed (R's cor() took 22 min at 50,000)
  rk <- function(X) { r <- apply(X, 2, rank); r <- sweep(r, 2, colMeans(r)); sweep(r, 2, sqrt(colSums(r^2)), "/") }
  C <- crossprod(rk(Ma), rk(Sa))                     # rows minfi, columns sesame
  dimnames(C) <- list(colnames(Ma), colnames(Sa))
  best <- rownames(C)[apply(C, 2, which.max)]
  marg <- vapply(seq_len(ncol(C)), function(j) C[j, j] - max(C[-j, j]), 0)
  list(n = sum(best == colnames(C)), tot = ncol(C), mind = min(diag(C)), marg = marg, C = C)
}
v <- apply(Ms, 1, var)
for (n in c(50000, 20000, 5000, 1000)) {
  r <- tm(sprintf("match_top%d", n), match_at(Ms[head(order(v, decreasing = TRUE), n), ], Ss[head(order(v, decreasing = TRUE), n), ]))
  ob(sprintf("  match_top%d", n), sprintf("%d/%d  min diag %.5f  per-sample margin min %.5f median %.5f", r$n, r$tot, r$mind, min(r$marg), median(r$marg)))
  if (n == 5000) r5 <- r
  if (n == 1000) {
    idx <- head(order(v, decreasing = TRUE), n)
    dv <- max(abs(r$C - cor(Ms[idx, ], Ss[idx, ], method = "spearman")))
    ck("crossprod_equals_cor_spearman", dv < 1e-12, sprintf("max |diff| %.2e over 689 x 689 at top 1000  want < 1e-12", dv))
  }
}
ck("pipelines_agree_on_identity", r5$n == r5$tot,
   sprintf("%d/%d samples' best rank-match is the same sample (top 5000 probes; %d of %d ordered pairs)", r5$n, r5$tot, r5$tot, r5$tot^2))
ob("identity_margin_min", sprintf("%.5f (the fixture's 6-sample margin was 0.11575)", min(r5$marg)))

if (length(fail)) { say("FAILED: ", paste(fail, collapse = ", ")); quit(status = 1) }
say("ALL CHECKS PASSED")
