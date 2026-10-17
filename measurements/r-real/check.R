# Smoke check for analysis.R on the 2023 TLC year. Rscript check.R <outdir>  (exit 1 on any failure)
#
# The exact references below were computed from the same 12 pinned files by DuckDB 1.5.6, an
# independent Parquet reader and aggregation engine (stage-inputs.sh documents the query). Money is
# summed in integer cents, so every one of them is an exact integer and must match bit for bit.
REF_ROWS        <- 38310226
REF_CENTS       <- 109038341189
REF_MODEL_ROWS  <- 34184942
REF_SHA_MONTHPT <- "ab6161bfa477d63c622b656999090a120d0741ef498aa00a39bab31c00fb2141"  # 62 groups
REF_SHA_ZONE    <- "fdbe9c62f1d883ed8870515a25a46fbc60d8d4803080b19bf46c2fcd2660b37b"  # 263 zones

outdir <- commandArgs(trailingOnly = TRUE)[1]
L <- readLines(file.path(outdir, "results.txt"))
kv <- setNames(sub("^\\S+ ?", "", L), sub(" .*$", "", L))
num <- function(k) as.numeric(strsplit(kv[[k]], " ")[[1]])
sha <- readLines(file.path(outdir, "agg.sha256"))
shaof <- function(f) sub(" .*$", "", grep(f, sha, value = TRUE, fixed = TRUE))
fail <- character(0)
check <- function(label, ok, detail) {
  cat(sprintf("%-22s %s %s\n", label, if (isTRUE(ok)) "ok  " else "FAIL", detail))
  if (!isTRUE(ok)) fail <<- c(fail, label)
}
eps <- .Machine$double.eps
n_mod <- num("N_MODEL_ROWS")

check("r_version", kv[["R_VERSION"]] == "4.5.3", kv[["R_VERSION"]])
check("r_arch", kv[["R_ARCH"]] == Sys.info()[["machine"]], paste(kv[["R_ARCH"]], "== host", Sys.info()[["machine"]]))
check("files", num("N_FILES") == 12, kv[["N_FILES"]])
check("rows_vs_footers", num("N_ROWS") == num("N_FOOTER_ROWS"), paste(kv[["N_ROWS"]], "decoded ==", kv[["N_FOOTER_ROWS"]], "in the Parquet footers"))
check("rows_vs_duckdb", num("N_ROWS") == REF_ROWS, paste(kv[["N_ROWS"]], "== DuckDB", REF_ROWS))
check("cents_unambiguous", num("MAX_CENT_RESIDUAL") < 1e-6, paste(kv[["MAX_CENT_RESIDUAL"]], "< 1e-6 (every total_amount is a whole number of cents)"))
check("cents_vs_duckdb", num("TOTAL_CENTS") == REF_CENTS, paste(kv[["TOTAL_CENTS"]], "== DuckDB", sprintf("%.0f", REF_CENTS)))
check("groups_conserve_n", all(num("GROUP_N_SUM") == num("N_ROWS")), paste(kv[["GROUP_N_SUM"]], "== N for both groupings"))
check("groups_conserve_cents", all(num("GROUP_CENTS_SUM") == num("TOTAL_CENTS")), paste(kv[["GROUP_CENTS_SUM"]], "== total for both groupings"))
check("datatable_eq_dplyr", kv[["DT_EQ_DPLYR"]] == "TRUE", "both tables, tolerance 0")
check("datatable_eq_base", kv[["DT_EQ_BASE"]] == "TRUE", "both tables vs rowsum(), exact")
check("month_payment_vs_ddb", shaof("agg-month-payment.csv") == REF_SHA_MONTHPT, paste(num("AGG_MONTH_PAYMENT_GROUPS"), "groups, sha256 == DuckDB's table"))
check("zone_vs_duckdb", shaof("agg-zone.csv") == REF_SHA_ZONE, paste(num("AGG_ZONE_GROUPS"), "zones, sha256 == DuckDB's table"))
check("model_rows_vs_ddb", n_mod == REF_MODEL_ROWS, paste(kv[["N_MODEL_ROWS"]], "== DuckDB", REF_MODEL_ROWS, "(same filters)"))
check("lm_full_rank", num("LM_RANK") == num("MODEL_COLS") && num("MODEL_COLS") == 32, paste(kv[["LM_RANK"]], "of", kv[["MODEL_COLS"]]))
# X'r = 0, to the rounding of a length-N inner product (~ sqrt(N) eps)
b1 <- sqrt(n_mod) * eps
check("normal_equations", num("MAX_COS_X_RESID") < b1, sprintf("max cos(X_j, r) %s < sqrt(N) eps = %.2e", kv[["MAX_COS_X_RESID"]], b1))
# lm's QR vs solve(X'X, X'y): the normal equations lose kappa^2 eps, and X'X is itself an N-term sum
b2 <- sqrt(n_mod) * num("KAPPA_X")^2 * eps
check("qr_vs_normeq", num("QR_VS_NORMEQ_REL") < b2, sprintf("%s < sqrt(N) kappa^2 eps = %.2e (kappa %s)", kv[["QR_VS_NORMEQ_REL"]], b2, kv[["KAPPA_X"]]))
check("r_squared_real", num("R_SQUARED") > 0.5 && num("R_SQUARED") < 1, kv[["R_SQUARED"]])
if (length(fail)) { cat("FAILED:", paste(fail, collapse = ", "), "\n"); quit(status = 1) }
cat("SMOKE CHECK PASSED\n")
