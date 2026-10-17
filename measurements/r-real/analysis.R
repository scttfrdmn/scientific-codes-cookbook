# A year of NYC yellow-taxi trips (TLC, 2023: 12 Parquet files, 38,310,226 rows) analysed the way
# an R user would: read with arrow, aggregate with data.table / dplyr / base, fit with lm().
#
#   Rscript analysis.R <dir-with-parquet> <outdir>
#
# Writes <outdir>/results.txt (KEY VALUE lines) and the two exact aggregate tables as CSV.
# check.R asserts on them; nothing here can fail the run except the analysis itself.
args <- commandArgs(trailingOnly = TRUE)
indir <- args[1]; outdir <- args[2]
Sys.setenv(TZ = "UTC")   # TLC timestamps are naive local time; read and keep them naive
suppressMessages({ library(arrow); library(data.table); library(dplyr) })
out <- character(0)
rec <- function(k, ...) { line <- paste(k, paste(vapply(list(...), function(x) paste(as.character(x), collapse = " "), ""), collapse = " ")); out <<- c(out, line); cat(line, "\n", sep = ""); flush(stdout()) }
tick <- function() unname(proc.time()[["elapsed"]])
rss_gib <- function() {   # this process's peak resident set, read from the kernel
  l <- grep("^VmHWM:", tryCatch(readLines("/proc/self/status"), error = function(e) ""), value = TRUE)
  if (length(l)) as.numeric(gsub("[^0-9]", "", l)) / 2^20 else NA_real_
}

rec("R_VERSION", getRversion())
rec("R_ARCH", R.version$arch)
rec("PKG_VERSIONS", sprintf("arrow=%s data.table=%s dplyr=%s", packageVersion("arrow"), packageVersion("data.table"), packageVersion("dplyr")))
rec("BLAS", extSoftVersion()[["BLAS"]])
rec("THREADS", sprintf("data.table=%d arrow=%d", getDTthreads(), arrow::cpu_count()))

# ---- read: 12 files, row counts from the Parquet footers (not the decoder) as the conservation
files <- sort(list.files(indir, pattern = "^yellow_tripdata_2023-[0-9]{2}\\.parquet$", full.names = TRUE))
rec("N_FILES", length(files))
footer_rows <- vapply(files, function(f) ParquetFileReader$create(f)$num_rows, numeric(1))
keep <- c("tpep_pickup_datetime", "tpep_dropoff_datetime", "trip_distance", "ratecodeid",
          "pulocationid", "payment_type", "fare_amount", "total_amount")
t0 <- tick()
dt <- rbindlist(lapply(files, function(f) {
  d <- as.data.table(read_parquet(f))
  setnames(d, tolower(names(d)))
  d <- d[, ..keep]
  d[, month := as.integer(sub(".*2023-([0-9]{2})\\.parquet$", "\\1", f))]
  d[, `:=`(pulocationid = as.integer(pulocationid), payment_type = as.integer(payment_type),
           ratecodeid = as.integer(ratecodeid))]
  d
}))
rec("T_READ_S", sprintf("%.1f", tick() - t0))
rec("N_ROWS", nrow(dt))
rec("N_FOOTER_ROWS", sprintf("%.0f", sum(footer_rows)))

# ---- exact aggregation: money in integer cents, so every partial sum is an exact integer (< 2^53)
# whatever the summation order -- three engines must then agree bit for bit, and the group totals
# must add back to the grand total exactly.
dt[, cents := round(total_amount * 100)]
rec("MAX_CENT_RESIDUAL", sprintf("%.3e", max(abs(dt$total_amount * 100 - dt$cents))))
total_cents <- sum(dt$cents)
rec("TOTAL_CENTS", sprintf("%.0f", total_cents))
t0 <- tick()
a_dt <- dt[, .(n = .N, cents = sum(cents)), keyby = .(month, payment_type)]
z_dt <- dt[, .(n = .N, cents = sum(cents)), keyby = .(pulocationid)]
rec("T_AGG_DATATABLE_S", sprintf("%.2f", tick() - t0))
t0 <- tick()
a_dp <- dt %>% group_by(month, payment_type) %>% summarise(n = n(), cents = sum(cents), .groups = "drop") %>% arrange(month, payment_type)
z_dp <- dt %>% group_by(pulocationid) %>% summarise(n = n(), cents = sum(cents), .groups = "drop") %>% arrange(pulocationid)
rec("T_AGG_DPLYR_S", sprintf("%.2f", tick() - t0))
t0 <- tick()
g_a <- interaction(dt$month, dt$payment_type, drop = TRUE, lex.order = TRUE)
g_z <- factor(dt$pulocationid)
a_bs <- rowsum(cbind(n = 1, cents = dt$cents), g_a, reorder = TRUE)
z_bs <- rowsum(cbind(n = 1, cents = dt$cents), g_z, reorder = TRUE)
rec("T_AGG_BASE_S", sprintf("%.2f", tick() - t0))
same <- function(x, y) isTRUE(all(as.numeric(x$n) == as.numeric(y[, "n"])) && all(x$cents == y[, "cents"]))
rec("AGG_MONTH_PAYMENT_GROUPS", nrow(a_dt))
rec("AGG_ZONE_GROUPS", nrow(z_dt))
rec("DT_EQ_DPLYR", isTRUE(all.equal(as.data.frame(a_dt), as.data.frame(a_dp), tolerance = 0, check.attributes = FALSE)) &&
                   isTRUE(all.equal(as.data.frame(z_dt), as.data.frame(z_dp), tolerance = 0, check.attributes = FALSE)))
rec("DT_EQ_BASE", same(a_dt, a_bs) && same(z_dt, z_bs))
rec("GROUP_N_SUM", sprintf("%.0f", sum(a_dt$n)), sprintf("%.0f", sum(z_dt$n)))
rec("GROUP_CENTS_SUM", sprintf("%.0f", sum(a_dt$cents)), sprintf("%.0f", sum(z_dt$cents)))
w <- function(d, f) writeLines(do.call(paste, c(lapply(d, function(v) sprintf("%.0f", as.numeric(v))), sep = ",")), file.path(outdir, f))
w(a_dt, "agg-month-payment.csv")
w(z_dt, "agg-zone.csv")

# ---- regression: metered fare on distance and time, hour-of-day and weekday effects
ok <- dt[ratecodeid == 1L & fare_amount > 0 & fare_amount <= 500 & trip_distance > 0 & trip_distance <= 100]
ok[, duration_min := as.numeric(difftime(tpep_dropoff_datetime, tpep_pickup_datetime, units = "mins"))]
ok <- ok[duration_min > 1 & duration_min <= 180]
ok[, `:=`(hour = factor(hour(tpep_pickup_datetime)), wday = factor(wday(tpep_pickup_datetime)))]
ok <- ok[, .(fare_amount, trip_distance, duration_min, hour, wday)]
rm(dt, a_dp, z_dp, g_a, g_z); invisible(gc())
rec("N_MODEL_ROWS", nrow(ok))
t0 <- tick()
fit <- lm(fare_amount ~ trip_distance + duration_min + hour + wday, data = ok, x = TRUE)
rec("T_LM_S", sprintf("%.1f", tick() - t0))
X <- fit$x; r <- residuals(fit); b_qr <- coef(fit)
rec("MODEL_COLS", ncol(X))
rec("LM_RANK", fit$rank)
rec("COEF_INTERCEPT", sprintf("%.10f", b_qr[["(Intercept)"]]))
rec("COEF_DISTANCE", sprintf("%.10f", b_qr[["trip_distance"]]))
rec("COEF_DURATION", sprintf("%.10f", b_qr[["duration_min"]]))
rec("R_SQUARED", sprintf("%.10f", summary(fit)$r.squared))
# normal equations: the defining property of the OLS solution is X'r = 0. Report each column's
# cosine with the residual vector, the scale-free form (a column in minutes and a 0/1 dummy alike).
t0 <- tick()
xtr <- drop(crossprod(X, r))
colnorm <- sqrt(colSums(X^2)); rnorm2 <- sqrt(sum(r^2))
rec("MAX_COS_X_RESID", sprintf("%.3e", max(abs(xtr) / (colnorm * rnorm2))))
rec("SUM_RESID_OVER_NORM", sprintf("%.3e", abs(sum(r)) / (sqrt(length(r)) * rnorm2)))
# a second, independent solve: X'X b = X'y through BLAS (dsyrk + Cholesky-free solve), not lm's
# LINPACK Householder QR. Its forward error grows like kappa(X)^2 * eps, so report that bound too.
b_ne <- drop(solve(crossprod(X), crossprod(X, ok$fare_amount)))
rec("T_NORMEQ_S", sprintf("%.1f", tick() - t0))
kap <- kappa(qr.R(fit$qr), exact = TRUE)
rec("KAPPA_X", sprintf("%.6e", kap))
rel <- max(abs(b_qr - b_ne)) / max(abs(b_qr))
rec("QR_VS_NORMEQ_REL", sprintf("%.3e", rel))
rec("KAPPA2_EPS", sprintf("%.3e", kap^2 * .Machine$double.eps))
writeLines(sprintf("%s %.15e", names(b_qr), b_qr), file.path(outdir, "coef.txt"))
rec("PEAK_RSS_GIB", sprintf("%.2f", rss_gib()))
writeLines(out, file.path(outdir, "results.txt"))
