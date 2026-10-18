# methylation-array-real, task 1 of 2 (minfi image): the whole GSE42861 cohort through the minfi
# workflow an EWAS actually runs -- read 689 IDAT pairs, sex check, functional normalisation, then
# a case/control DMP scan -- with the recipe's identities carried to cohort scale.
#
#   Rscript minfi-real.R      (inputs in /tmp: idat/, pheno.tsv; writes /tmp/out/)
suppressMessages(library(minfi))
O <- "/tmp/out"; dir.create(O, showWarnings = FALSE)
out <- file(file.path(O, "checks.txt"), open = "wt")
say <- function(...) { s <- paste0(...); cat(s, "\n", sep = ""); cat(s, "\n", sep = "", file = out); flush(out) }
fail <- character(0)
# isTRUE: NA and NaN fail, never pass (a NaN is not > anything, so a one-sided test would wave it through)
ck <- function(k, ok, v) { say(sprintf("%-34s %s %s", k, if (isTRUE(ok)) "ok  " else "FAIL", v)); if (!isTRUE(ok)) fail <<- c(fail, k) }
ob <- function(k, v) say(sprintf("%-34s %s   [observation]", k, v))
T <- file(file.path(O, "times.txt"), open = "wt")
tm <- function(label, expr) { t0 <- Sys.time(); v <- force(expr)
  s <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  cat(sprintf("%-22s %8.1f s\n", label, s)); cat(sprintf("%s\t%.1f\n", label, s), file = T); flush(T); v }
pv <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) "unknown")
for (p in c("minfi", "IlluminaHumanMethylation450kmanifest", "IlluminaHumanMethylation450kanno.ilmn12.hg19", "limma"))
  say(sprintf("%-34s %s", paste0(p, "_version"), pv(p)))
say(sprintf("%-34s %s", "blas", tryCatch(as.character(extSoftVersion()[["BLAS"]]), error = function(e) "unknown")))

pd <- read.delim("/tmp/pheno.tsv", colClasses = "character")
ck("samples", nrow(pd) == 689 && length(unique(pd$gsm)) == 689 && length(unique(pd$basename)) == 689,
   sprintf("%d rows, %d unique GSM, %d unique basenames  want 689 each", nrow(pd), length(unique(pd$gsm)), length(unique(pd$basename))))
tb <- table(pd$disease_state); say(sprintf("%-34s %s", "design", paste(names(tb), tb, sep = "=", collapse = "  ")))

RG <- tm("read.metharray", read.metharray(file.path("/tmp/idat", pd$basename)))
colnames(RG) <- pd$gsm
ck("arrays_read", ncol(RG) == 689, sprintf("%d  want 689", ncol(RG)))
ck("array_type", identical(unname(annotation(RG)["array"]), "IlluminaHumanMethylation450k"),
   sprintf("%s / %s", annotation(RG)["array"], annotation(RG)["annotation"]))

raw <- tm("preprocessRaw", preprocessRaw(RG))
b <- getBeta(raw)
ck("probes", nrow(b) == 485512, sprintf("%d  want exactly 485512 (450k array)", nrow(b)))
rng <- range(b, na.rm = TRUE)
ck("beta_in_range", rng[1] >= 0 && rng[2] <= 1,
   sprintf("[%.6f, %.6f] over %s values  want within [0,1]", rng[1], rng[2], format(length(b), big.mark = ",")))
# beta = M/(M+U) with no offset is 0/0 where a bead type read zero in both channels
ob("raw_beta_NA", sprintf("%d (M+U == 0: %d)", sum(is.na(b)), sum(getMeth(raw) + getUnmeth(raw) == 0)))

# TRUTH 1 at cohort scale: sex from X/Y intensity against the depositors' annotation. Mismatches in
# a real cohort are sample-sheet facts, not tool errors, so they are listed for task 2 to re-call
# with SeSAMe's independent method rather than asserted away here.
gs <- tm("getSex", getSex(mapToGenome(raw)))
ann <- toupper(pd$gender)
ck("annotated_sex_values", all(ann %in% c("F", "M")), paste(names(table(ann)), table(ann), sep = "=", collapse = " "))
agree <- gs$predictedSex == ann
ob("minfi_sex_vs_annotation", sprintf("%d/%d agree; %d disagree", sum(agree), length(agree), sum(!agree)))
write.table(data.frame(gsm = pd$gsm, annotated = ann, minfi = gs$predictedSex, xMed = round(gs$xMed, 4), yMed = round(gs$yMed, 4)),
            file.path(O, "sex-minfi.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
if (any(!agree)) for (i in which(!agree)) ob("  sex_mismatch", sprintf("%s annotated %s, minfi %s (yMed-xMed %.3f)", pd$gsm[i], ann[i], gs$predictedSex[i], gs$yMed[i] - gs$xMed[i]))

# TRUTH 2's platform half: the 65 identity probes. This cohort has one sample per person, so there is
# no donor pairing to recover; the nearest pairs are reported, which is how a sample swap or a
# duplicate submission would show up.
sb <- getSnpBeta(RG)
ck("snp_probes", nrow(sb) == 65, sprintf("%d  want exactly 65", nrow(sb)))
d <- as.matrix(dist(t(sb))); diag(d) <- Inf
nn <- apply(d, 1, min)
ob("snp_nn_distance", sprintf("min %.4f  median %.4f  max %.4f  (fixture: within-donor median 0.5951, between 3.2501)", min(nn), median(nn), max(nn)))
ut <- which(upper.tri(d), arr.ind = TRUE); dv <- d[ut]; o <- order(dv)[1:5]
for (k in o) ob("  snp_closest_pair", sprintf("%s %s %.4f", rownames(d)[ut[k, 1]], colnames(d)[ut[k, 2]], dv[k]))
ob("snp_pairs_below_1.0", sum(dv < 1.0))
rm(d)

# For task 2 (SeSAMe image, which carries no minfi): the raw betas, unrounded. Written now so the
# 8 GB of raw intensities can be freed before functional normalisation, the memory peak.
invisible(tm("export", saveRDS(b, "/tmp/out/minfi-raw-betas.rds", compress = FALSE)))
rm(raw, b); invisible(gc())

# The cohort-scale step: functional normalisation uses the control probes of all 689 arrays at once.
fn <- tm("preprocessFunnorm", preprocessFunnorm(RG, nPCs = 2, verbose = FALSE))
rm(RG); invisible(gc())
bf <- getBeta(fn)
ck("funnorm_dims", nrow(bf) == 485512 && ncol(bf) == 689, sprintf("%d x %d  want 485512 x 689", nrow(bf), ncol(bf)))
rf <- range(bf, na.rm = TRUE)
ck("funnorm_beta_in_range", rf[1] >= 0 && rf[2] <= 1, sprintf("[%.6f, %.6f], %d NA", rf[1], rf[2], sum(is.na(bf))))
if ("predictedSex" %in% colnames(colData(fn)))
  ob("funnorm_sex_vs_raw_getSex", sprintf("%d/%d identical", sum(fn$predictedSex == gs$predictedSex), 689))
rm(bf)

# EWAS: rheumatoid arthritis vs control on M-values. dmpFinder's categorical F comes from two lmFit
# residual sums of squares; for two groups it must equal the square of the pooled two-sample t,
# computed here from row means and variances alone -- a closed form, no tolerance band.
M <- getM(fn)
# funnorm returns some betas of exactly 0, whose logit is -Inf; those probes cannot enter a linear model
fin <- rowSums(!is.finite(M)) == 0
ob("probes_dropped_nonfinite_M", sprintf("%d probes (%d values of M infinite)", sum(!fin), sum(!is.finite(M))))
M <- M[fin, ]
grp <- factor(pd$disease_state)
dmp <- tm("dmpFinder", dmpFinder(M, pheno = grp, type = "categorical"))
ck("dmp_rows", nrow(dmp) == nrow(M) && nrow(M) > 480000, sprintf("%d  want one per finite probe (%d)", nrow(dmp), nrow(M)))
tt <- tm("closed_form_t", {
  g1 <- grp == levels(grp)[1]; n1 <- sum(g1); n2 <- sum(!g1)
  m1 <- rowMeans(M[, g1]); m2 <- rowMeans(M[, !g1])
  ss <- rowSums((M[, g1] - m1)^2) + rowSums((M[, !g1] - m2)^2)
  (m1 - m2) / sqrt(ss / (n1 + n2 - 2) * (1 / n1 + 1 / n2))
})
F <- dmp[rownames(M), "f"]
# relative where F >= 1, absolute below it (an F near 0 is two roundings of nothing)
rel <- abs(F - tt^2) / pmax(tt^2, 1)
ck("dmp_F_equals_pooled_t_squared", !anyNA(rel) && max(rel) < 1e-8,
   sprintf("max diff %.2e over %d probes (relative, absolute below F=1)  want < 1e-8", max(rel), length(rel)))
df2 <- length(grp) - 2
pr <- abs(dmp$pval - pf(dmp$f, 1, df2, lower.tail = FALSE)) / pmax(dmp$pval, 1e-300)
ck("dmp_p_from_F", !anyNA(pr) && max(pr) < 1e-10, sprintf("p == pf(F, 1, %d, upper): max rel %.2e  want < 1e-10", df2, max(pr)))
ob("dmp_q_lt_0.05", sum(dmp$qval < 0.05))
ob("dmp_bonferroni_0.05", sum(dmp$pval < 0.05 / nrow(dmp)))
ob("dmp_top", sprintf("%s F=%.3f p=%.3e", rownames(dmp)[1], dmp$f[1], dmp$pval[1]))
write.table(cbind(probe = rownames(dmp)[1:1000], signif(dmp[1:1000, ], 8)), file.path(O, "dmp-top1000.tsv"),
            sep = "\t", quote = FALSE, row.names = FALSE)
rm(M, fn)

say(sprintf("%-34s %.2f", "r_max_vcells_gib", sum(gc()[, ncol(gc())]) / 1024))

if (length(fail)) { say("FAILED: ", paste(fail, collapse = ", ")); quit(status = 1) }
say("ALL CHECKS PASSED")
