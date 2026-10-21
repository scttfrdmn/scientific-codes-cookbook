# r-arrow-real: 36 NYC TLC yellow-taxi Parquet files (2009, 2010, 2023; 378 M trips) queried from R
# with Arrow's dataset API and dplyr, rewritten as one hive-partitioned dataset and queried again.
#
#   Rscript analysis.R <dir of the 36 files> <out dir> <threads>
#
# The workload (timed, TOOL_WALL_S) is what an Arrow user does with this data:
#   * open_dataset() over each schema era's 12 files and four dplyr queries pushed into Acero, with
#     add_filename() to keep per-file provenance: per-file totals, payment type, pickup hour (parsed
#     from strings with strptime in 2009-2010), and a filtered trip-distance histogram;
#   * write_dataset() of every file into ONE year=/month= hive-partitioned dataset with a common
#     schema, then the same four queries over that single dataset, and a partition-pruned count.
# Money is integer cents, as.integer64(floor(x*100 + 0.5)), so every sum is exact in any order.
#
# Every table is then checked (TOOL wall excludes this):
#   * rows: per-file count == the Parquet footer; the rewrite's footers sum to the same total;
#   * the rewrite == the raw queries, every row of every table (a write/read round trip at scale);
#   * Acero == data.table on the same decoded columns, file by file (an independent engine);
#   * Acero == polars 2.0.0 (an independent Parquet reader AND engine: reference.py, run once
#     in its own task), line for line;
#   * conservation: each table's groups add back to the per-file totals.
suppressMessages({library(arrow); library(dplyr); library(data.table); library(bit64)})
options(arrow.int64_downcast = FALSE, scipen = 999)
a <- commandArgs(TRUE); IN <- a[1]; OUT <- a[2]; NT <- as.integer(a[3])
set_cpu_count(NT)
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)
REF <- Sys.getenv("REF_DIR", "")
LOG <- file.path(OUT, "check.txt"); cat("", file = LOG)
rec <- new.env()
say <- function(...) { s <- paste0(...); cat(s, "\n", sep = ""); cat(s, "\n", sep = "", file = LOG, append = TRUE) }
put <- function(k, v) { assign(k, as.character(v), envir = rec); say(k, "\t", as.character(v)) }
fails <- 0L
check <- function(ok, what) {
  ok <- isTRUE(as.logical(ok))
  say(if (ok) "PASS  " else "FAIL  ", what)
  if (!ok) fails <<- fails + 1L
}
ver <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) "unknown")
put("arrow", ver("arrow")); put("libarrow", tryCatch(arrow_info()$build_info$cpp_version, error = function(e) "unknown"))
put("dplyr", ver("dplyr")); put("data.table", ver("data.table")); put("R", R.version.string)
put("cpu_count", cpu_count()); put("io_thread_count", io_thread_count())
put("longdouble.digits", .Machine$longdouble.digits)

ERA <- list(
  "2009" = list(pu = "Trip_Pickup_DateTime", pax = "Passenger_Count", dist = "Trip_Distance",
                pay = "Payment_Type", fare = "Fare_Amt", tip = "Tip_Amt", tot = "Total_Amt"),
  "2010" = list(pu = "pickup_datetime", pax = "passenger_count", dist = "trip_distance",
                pay = "payment_type", fare = "fare_amount", tip = "tip_amount", tot = "total_amount"),
  "2023" = list(pu = "tpep_pickup_datetime", pax = "passenger_count", dist = "trip_distance",
                pay = "payment_type", fare = "fare_amount", tip = "tip_amount", tot = "total_amount"))
files <- sort(list.files(IN, pattern = "^yellow_tripdata_.*\\.parquet$", full.names = TRUE))
EXPECT <- as.integer(Sys.getenv("EXPECT_FILES", "36"))
check(length(files) == EXPECT, sprintf("%d Parquet files staged (expected %d)", length(files), EXPECT))
# year/month come from the file name; every path has the same length, so fixed positions are exact
L <- nchar(files[1]); check(all(nchar(files) == L), "all staged paths have one length")
YP <- as.integer(L - nchar("2009-01.parquet") + 1L)
yr_of <- function(f) as.integer(substr(f, YP, YP + 3)); mo_of <- function(f) as.integer(substr(f, YP + 5, YP + 6))
eras <- intersect(names(ERA), unique(as.character(yr_of(files))))

tm <- list(); tic <- function(k, expr) { t0 <- proc.time()[["elapsed"]]; v <- force(expr); tm[[k]] <<- proc.time()[["elapsed"]] - t0; v }
# integer cents as an expression (NaN/Inf -> null), injected with !! so Acero evaluates it
cents <- function(v) rlang::expr(bit64::as.integer64(floor(if_else(is.finite(!!v), !!v, NA_real_) * 100 + 0.5)))

# --- footers -------------------------------------------------------------------------------------
footer <- tic("footers", vapply(files, function(f) ParquetFileReader$create(f)$num_rows, numeric(1)))
put("rows_footer_total", sum(footer))

# --- the raw query, one per era (each era is 12 files with one schema, or a castable one) -----------
raw_q <- function(e, fs = files[yr_of(files) == as.integer(e)], with_file = TRUE) {
  c <- ERA[[e]]
  d <- open_dataset(fs)            # the FIRST file's schema; 2023's later months are cast to it
  pu <- if (e == "2023") quote(hour(pu)) else quote(hour(strptime(pu, format = "%Y-%m-%d %H:%M:%S", tz = "UTC", unit = "s")))
  q <- d |> select(pu = all_of(c$pu), pax = all_of(c$pax), dist = all_of(c$dist), pay = all_of(c$pay),
                   fare = all_of(c$fare), tip = all_of(c$tip), tot = all_of(c$tot))
  # add_filename() is only grouped on, never computed with: Acero does not know its type
  if (with_file) q <- q |> mutate(file = add_filename())
  q |> mutate(year = !!as.integer(e), hour = as.integer(!!pu), pax = as.numeric(pax), pay = as.character(pay),
              fare_c = !!cents(quote(fare)), tip_c = !!cents(quote(tip)), tot_c = !!cents(quote(tot))) |>
    select(any_of("file"), year, hour, pax, dist, pay, fare_c, tip_c, tot_c)
}
# the four tables; `by_file` is per file on the raw data and per (year, month) on the rewrite
tables <- function(q, by_file) {
  k <- if (by_file) c("file") else c("year", "month")
  list(
    files = q |> group_by(!!!rlang::syms(k)) |>
      summarise(n = n(), n_tot = sum(!is.na(tot_c)), tot = sum(tot_c, na.rm = TRUE),
                tip = sum(tip_c, na.rm = TRUE), fare = sum(fare_c, na.rm = TRUE),
                pax = sum(pax, na.rm = TRUE), max_tot = max(tot_c, na.rm = TRUE),
                min_tot = min(tot_c, na.rm = TRUE), .groups = "drop") |> collect(),
    pay = q |> mutate(pay = coalesce(pay, "<null>")) |> group_by(year, pay) |>
      summarise(n = n(), tot_c = sum(tot_c, na.rm = TRUE), .groups = "drop") |> collect(),
    hour = q |> mutate(hour = coalesce(hour, -1L)) |> group_by(year, hour) |>
      summarise(n = n(), fare_c = sum(fare_c, na.rm = TRUE), .groups = "drop") |> collect(),
    dist = q |> filter(dist > 0, dist <= 100, fare_c > 0L, fare_c <= 50000L) |>
      mutate(bin = as.integer(floor(dist))) |> group_by(year, bin) |>
      summarise(n = n(), fare_c = sum(fare_c, na.rm = TRUE), .groups = "drop") |> collect())
}
# canonical text: keys then values, integers printed exactly, rows sorted bytewise (radix = C locale)
canon <- function(t, keys) {
  t <- as.data.frame(t)
  if ("file" %in% names(t)) t$file <- basename(t$file)
  for (v in setdiff(names(t), c(keys, "pay"))) t[[v]] <- as.character(as.integer64(t[[v]]))
  t <- t[do.call(order, c(lapply(keys, function(k) t[[k]]), method = "radix")), , drop = FALSE]
  c(paste(names(t), collapse = ","), do.call(paste, c(lapply(t, as.character), sep = ",")))
}
KEYS <- list(files = "file", pay = c("year", "pay"), hour = c("year", "hour"), dist = c("year", "bin"))
bind <- function(l) lapply(setNames(names(KEYS), names(KEYS)), function(k) bind_rows(lapply(l, `[[`, k)))

raw <- tic("raw_queries", bind(lapply(eras, function(e) tables(raw_q(e), TRUE))))
RAW <- lapply(setNames(names(KEYS), names(KEYS)), function(k) canon(raw[[k]], KEYS[[k]]))

# --- rewrite: one hive-partitioned dataset with a common schema, then the same queries -------------
RW <- file.path(Sys.getenv("RW_DIR", "/tmp/rw"))
unlink(RW, recursive = TRUE)
invisible(tic("write_dataset", for (f in files) raw_q(as.character(yr_of(f)), f, with_file = FALSE) |>
  mutate(month = !!mo_of(f)) |>
  write_dataset(RW, format = "parquet", partitioning = c("year", "month"),
                basename_template = sub("\\.parquet$", "-{i}.parquet", basename(f)), existing_data_behavior = "overwrite")))
rw <- open_dataset(RW)       # year=/month= detected as hive partitions
rwt <- tic("rewrite_queries", tables(rw, FALSE))
pr_y <- yr_of(files[length(files)]); pr_m <- mo_of(files[length(files)])
pruned <- tic("pruned_count", rw |> filter(year == pr_y, month == pr_m) |> count() |> collect() |> pull(n))
put("TOOL_WALL_S", sprintf("%.2f", sum(unlist(tm))))
for (k in names(tm)) put(paste0("time_", k, "_s"), sprintf("%.2f", tm[[k]]))

# --- checks --------------------------------------------------------------------------------------
say("== rows ==")
fT <- raw$files; fT$f <- basename(fT$file)
nf <- setNames(as.numeric(fT$n), fT$f)
check(length(nf) == length(files) && all(nf[basename(files)] == footer), sprintf("rows per file == Parquet footer, %d files", length(files)))
put("rows_read_total", sum(nf))
rwf <- list.files(RW, recursive = TRUE, full.names = TRUE)
put("rewrite_files", length(rwf)); put("rewrite_bytes", sum(file.size(rwf)))
rw_footer <- sum(vapply(rwf, function(f) ParquetFileReader$create(f)$num_rows, numeric(1)))
check(rw_footer == sum(footer), sprintf("rewrite's footers sum to the input's (%s)", format(rw_footer)))
check(pruned == footer[length(footer)], sprintf("pruned count year=%d/month=%d == that file's footer (%s)", pr_y, pr_m, format(pruned)))
check(length(list.files(file.path(RW, paste0("year=", pr_y), paste0("month=", pr_m)))) >= 1, "the pruned partition exists as a directory")

say("== rewrite == raw (write/read round trip) ==")
rf <- raw$files; rf$year <- yr_of(rf$file); rf$month <- mo_of(rf$file); rf$file <- NULL
RAWYM <- canon(rf[, c("year", "month", setdiff(names(rf), c("year", "month")))], c("year", "month"))
check(identical(RAWYM, canon(rwt$files, c("year", "month"))), "per-month totals: rewrite == raw, every line")
for (k in c("pay", "hour", "dist"))
  check(identical(RAW[[k]], canon(rwt[[k]], KEYS[[k]])), sprintf("%s table: rewrite == raw, %d lines", k, length(RAW[[k]]) - 1))

say("== conservation ==")
i64 <- function(x) sum(as.integer64(x))
for (k in c("pay", "hour")) {
  t <- raw[[k]]; v <- if (k == "pay") "tot_c" else "fare_c"
  check(i64(t$n) == i64(raw$files$n), sprintf("%s: group counts add to the rows", k))
  check(i64(t[[v]]) == i64(raw$files[[sub("_c$", "", v)]]), sprintf("%s: group %s adds to the per-file total", k, v))
}
check(i64(raw$dist$n) <= i64(raw$files$n), "distance histogram counts no more than the rows")

say("== Acero == data.table, file by file on the same decoded columns ==")
t0 <- proc.time()[["elapsed"]]
dtp <- list()
for (f in files) {
  e <- as.character(yr_of(f)); c <- ERA[[e]]
  x <- as.data.table(read_parquet(f, col_select = all_of(unname(unlist(c)))))
  setnames(x, unname(unlist(c)), names(c))
  ok <- if (is.character(x$pu)) grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$", x$pu) else !is.na(x$pu)
  hr <- if (is.character(x$pu)) as.integer(substr(x$pu, 12, 13)) else as.integer((floor(as.numeric(x$pu)) %/% 3600) %% 24)
  hr[!ok | is.na(hr) | hr > 23L] <- -1L
  ce <- function(v) { v <- floor(v * 100 + 0.5); v[!is.finite(v)] <- NA; as.integer64(v) }
  x[, `:=`(year = as.integer(e), file = basename(f), hour = hr, pax = as.numeric(pax),
           pay = fifelse(is.na(pay), "<null>", as.character(pay)), fare_c = ce(fare), tip_c = ce(tip), tot_c = ce(tot))]
  dtp[[f]] <- list(
    files = x[, .(n = .N, n_tot = sum(!is.na(tot_c)), tot = sum(tot_c, na.rm = TRUE), tip = sum(tip_c, na.rm = TRUE),
                  fare = sum(fare_c, na.rm = TRUE), pax = sum(pax, na.rm = TRUE),
                  max_tot = max(tot_c, na.rm = TRUE), min_tot = min(tot_c, na.rm = TRUE)), by = file],
    pay = x[, .(n = .N, tot_c = sum(tot_c, na.rm = TRUE)), by = .(year, pay)],
    hour = x[, .(n = .N, fare_c = sum(fare_c, na.rm = TRUE)), by = .(year, hour)],
    dist = x[dist > 0 & dist <= 100 & !is.na(fare_c) & fare_c > 0 & fare_c <= 50000,
             .(n = .N, fare_c = sum(fare_c)), by = .(year, bin = as.integer(floor(dist)))])
  rm(x); invisible(gc())
}
comb <- function(k, keys, vals) {
  t <- rbindlist(lapply(dtp, `[[`, k))
  t[, lapply(.SD, function(v) sum(as.integer64(v))), by = keys, .SDcols = vals]
}
DT <- list(files = canon(rbindlist(lapply(dtp, `[[`, "files")), "file"),
           pay = canon(comb("pay", c("year", "pay"), c("n", "tot_c")), KEYS$pay),
           hour = canon(comb("hour", c("year", "hour"), c("n", "fare_c")), KEYS$hour),
           dist = canon(comb("dist", c("year", "bin"), c("n", "fare_c")), KEYS$dist))
put("time_datatable_check_s", sprintf("%.2f", proc.time()[["elapsed"]] - t0))
for (k in names(KEYS)) check(identical(RAW[[k]], DT[[k]]), sprintf("%s table: Acero == data.table, %d lines", k, length(RAW[[k]]) - 1))

say("== Acero == polars (independent reader and engine) ==")
if (nzchar(REF)) {
  for (k in names(KEYS)) {
    p <- readLines(file.path(REF, paste0(k, ".csv")))
    check(identical(RAW[[k]], p), sprintf("%s table: Acero == polars, %d lines", k, length(p) - 1))
    if (!identical(RAW[[k]], p)) { d <- setdiff(union(RAW[[k]], p), intersect(RAW[[k]], p)); say("  differs: ", paste(head(d, 6), collapse = " | ")) }
  }
} else say("SKIP  no polars reference staged")

for (k in names(KEYS)) writeLines(RAW[[k]], file.path(OUT, paste0(k, ".csv")))
for (k in names(KEYS)) put(paste0("sha256_", k), digest::digest(file = file.path(OUT, paste0(k, ".csv")), algo = "sha256"))
put("hour_unparsed_rows", sum(as.integer64(raw$hour$n[raw$hour$hour == -1L])))
put("cents_total", as.character(i64(raw$files$tot)))
st <- readLines("/proc/self/status"); put("VmHWM", trimws(sub("VmHWM:", "", grep("^VmHWM", st, value = TRUE))))
writeLines(vapply(sort(ls(rec)), function(k) paste0(k, "\t", get(k, envir = rec)), ""), file.path(OUT, "results.tsv"))
if (fails == 0L) say("R-ARROW REAL OK") else { say(fails, " check(s) FAILED"); quit(status = 1) }
