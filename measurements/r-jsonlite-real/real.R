# jsonlite on the whole OpenAlex `institutions` entity (release 2026-10-14, 83 JSON Lines parts,
# 143,455 records). Every check runs on every record; nothing is sampled.
#
#   Rscript real.R <parts dir> <manifest.json> <work dir> <out dir> [part indices, e.g. 82,83]
#
#   counts      lines per part == the manifest's record_count == jsonlite's rows == Arrow's rows
#   round trip  parse -> toJSON -> parse is identical() on every record with digits = I(17) and
#               always_decimal = TRUE; the default, digits = NA and I(17) alone are counted, not
#               asserted (the recipe's findings, at scale)
#   referee     Arrow's C++ JSON reader on the same bytes: ids, names, alternative names, counts,
#               coordinates and per-year totals equal jsonlite's, bit for bit
#   user path   stream_in of the whole entity into one data.frame, stream_out and back (recorded)
#
# The per-record work runs in forked workers over 2,000-record chunks (parallel::mclapply), one per
# vCPU. Each worker reads its own chunk file, so the parent holds no parsed records when it forks.
suppressPackageStartupMessages({ library(jsonlite); library(arrow); library(parallel) })
a <- commandArgs(TRUE)
IN <- a[1]; MAN <- a[2]; WORK <- a[3]; OUT <- a[4]
SEL <- if (length(a) >= 5) as.integer(strsplit(a[5], ",")[[1]]) else NULL
CORES <- detectCores()

score <- file.path(OUT, "score.tsv")
cat("", file = score)
rec <- function(k, ...) {                       # str() every value: a report line must never fail
  v <- paste(vapply(list(...), function(x) paste(as.character(x), collapse = ","), ""), collapse = " ")
  cat(k, "\t", v, "\n", sep = "", file = score, append = TRUE)
  cat(sprintf("%-60s %s\n", k, v)); flush.console()
}
fails <- character()
check <- function(name, ok, ...) {
  ok <- isTRUE(as.logical(ok))
  rec(paste0(if (ok) "PASS " else "FAIL ", name), ...)
  if (!ok) fails <<- c(fails, name)
}
LAST <- NA_real_
phase <- function(name, expr) {
  t <- system.time(v <- force(expr))[["elapsed"]]
  LAST <<- t
  rec(paste0("time_s ", name), sprintf("%.2f", t))
  v
}
rec("jsonlite", as.character(packageVersion("jsonlite")))
rec("arrow", as.character(packageVersion("arrow")))
rec("R", R.version$version.string)
rec("arch", R.version$arch)
rec("workers (detectCores)", CORES)

# ---- counts against the source ---------------------------------------------------------------
m <- fromJSON(MAN)
rel <- sub("^s3://openalex/data/jsonl/institutions/", "", m$files$url)
want <- as.integer(m$files$meta$record_count)
check("manifest: 83 parts, record_count 143455", m$record_count == 143455L &&
      sum(want) == 143455L && length(rel) == 83L, length(rel), m$record_count)
if (!is.null(SEL)) { rel <- rel[SEL]; want <- want[SEL]; rec("PROBE: parts only", SEL) }
N <- sum(want)

L <- phase("readLines (gz parts)", lapply(rel, function(p)
  readLines(gzfile(file.path(IN, p)), encoding = "UTF-8", warn = FALSE)))
got <- lengths(L)
check("lines per part == manifest record_count, every part", all(got == want),
      sum(got == want), "of", length(got), "parts;", sum(got), "lines")
nbytes <- sum(vapply(L, function(x) sum(as.numeric(nchar(x, "bytes"))) + length(x), 0))
rec("uncompressed JSON bytes", sprintf("%.0f", nbytes))

dir.create(WORK, showWarnings = FALSE, recursive = TRUE)
partf <- file.path(WORK, sprintf("part%02d.ndjson", seq_along(L)))
for (k in seq_along(L)) writeLines(L[[k]], partf[k], useBytes = TRUE)
all_f <- file.path(WORK, "institutions.ndjson")
invisible(file.create(all_f)); invisible(file.append(all_f, partf))
Lall <- unlist(L, use.names = FALSE); rm(L)
first_id <- sub('^\\{"id":"([^"]*)".*$', "\\1", Lall, perl = TRUE)
CH <- 2000L
starts <- seq(1L, N, by = CH)
chunkf <- file.path(WORK, sprintf("chunk%03d.ndjson", seq_along(starts)))
for (k in seq_along(starts)) writeLines(Lall[starts[k]:min(N, starts[k] + CH - 1L)], chunkf[k], useBytes = TRUE)
rm(Lall); invisible(gc())
rec("chunks", length(chunkf), "of", CH, "records")

# ---- per record: parse, round trip four ways, re-emit, extract referee fields ----------------
num <- function(v) if (is.null(v)) NA_real_ else as.numeric(v)
work <- function(k) {
  src <- readLines(chunkf[k], encoding = "UTF-8", warn = FALSE)
  t0 <- proc.time()[["elapsed"]]
  X <- lapply(src, parse_json)
  t_parse <- proc.time()[["elapsed"]] - t0
  rt <- function(...) {
    txt <- toJSON(X, auto_unbox = TRUE, null = "null", ...)
    Y <- parse_json(txt)
    if (length(Y) != length(X)) stop("round trip changed the record count in chunk ", k)
    ok <- mapply(identical, X, Y, USE.NAMES = FALSE)
    list(bad = sum(!ok), first = if (any(!ok)) X[[which(!ok)[1]]]$id else NA_character_, txt = txt)
  }
  t0 <- proc.time()[["elapsed"]]
  A <- rt(); B <- rt(digits = NA); C <- rt(digits = I(17))
  D <- rt(digits = I(17), always_decimal = TRUE)
  t_rt <- proc.time()[["elapsed"]] - t0
  writeLines(D$txt, file.path(WORK, sprintf("lossless%03d.json", k)), useBytes = TRUE)
  t0 <- proc.time()[["elapsed"]]
  na_txt <- vapply(X, function(r) as.character(toJSON(r, auto_unbox = TRUE, null = "null",
    digits = NA, always_decimal = TRUE)), "")
  same <- na_txt == src
  na_ok <- mapply(function(x, t) identical(x, parse_json(t)), X, na_txt, USE.NAMES = FALSE)
  t_same <- proc.time()[["elapsed"]] - t0
  jl <- data.frame(
    id = vapply(X, function(r) r$id, ""),
    name = vapply(X, function(r) enc2utf8(r$display_name), ""),
    works = vapply(X, function(r) num(r$works_count), 0),
    cites = vapply(X, function(r) num(r$cited_by_count), 0),
    lat = vapply(X, function(r) num(r$geo$latitude), 0),
    lon = vapply(X, function(r) num(r$geo$longitude), 0),
    alts = vapply(X, function(r) paste(enc2utf8(as.character(unlist(r$display_name_alternatives))), collapse = "\x1f"), ""),
    cby = vapply(X, function(r) sum(vapply(r$counts_by_year, function(y) num(y$works_count), 0)), 0),
    stringsAsFactors = FALSE)
  list(n = length(X), bad = c(A$bad, B$bad, C$bad, D$bad), first = c(A$first, B$first, C$first, D$first),
       same = sum(same), na_bad = sum(!na_ok), jl = jl, t = c(t_parse, t_rt, t_same))
}
R <- phase("per-record work (mclapply)", mclapply(seq_along(chunkf), work, mc.cores = CORES,
                                                  mc.preschedule = FALSE))
err <- vapply(R, function(r) inherits(r, "try-error") || is.null(r$n), NA)
check("every chunk worker completed", !any(err), sum(!err), "of", length(R))
if (any(err)) { print(R[err][[1]]); quit(status = 1) }
tw <- Reduce(`+`, lapply(R, `[[`, "t"))
rec("worker-seconds: parse_json, 4 round trips, per-record re-emit", sprintf("%.1f", tw))
check("records parsed == source", sum(vapply(R, `[[`, 0L, "n")) == N, sum(vapply(R, `[[`, 0L, "n")))
bad <- Reduce(`+`, lapply(R, `[[`, "bad"))
firsts <- do.call(rbind, lapply(R, `[[`, "first"))
lab <- c("default (digits 4)", "digits = NA", "digits = I(17)", "I(17) + always_decimal")
for (j in 1:4) rec(paste0("round trip ", lab[j], ": records not identical"), bad[j], "of", N,
                   "first", na.omit(firsts[, j])[1])
check("lossless round trip: I(17) + always_decimal, every record", bad[4] == 0, bad[4], "not identical")
check("control: the default is lossy at scale", bad[1] > 0, bad[1], "records changed")
rec("round trip digits = NA + always_decimal: records not identical",
    sum(vapply(R, `[[`, 0L, "na_bad")), "of", N)
rec("re-serialized byte-identical to the source line (recorded)",
    sum(vapply(R, `[[`, 0L, "same")), "of", N)
lossless <- file.path(WORK, "lossless.json")
invisible(file.create(lossless))
invisible(file.append(lossless, file.path(WORK, sprintf("lossless%03d.json", seq_along(chunkf)))))
jl <- do.call(rbind, lapply(R, `[[`, "jl")); rm(R); invisible(gc())
check("ids in source order", identical(jl$id, first_id), "parse_json ids == each line's leading \"id\"")

# ---- the referee: Arrow's C++ reader on the same bytes ----------------------------------------
ar <- phase("arrow read_json_arrow, every part", do.call(rbind, lapply(seq_along(partf), function(k) {
  tab <- read_json_arrow(partf[k], as_data_frame = FALSE,
                         read_options = JsonReadOptions$create(block_size = as.integer(file.size(partf[k]) + 1)))
  if (tab$num_rows != want[k]) stop("arrow rows != manifest in part ", k)
  d <- as.data.frame(tab[, c("id", "display_name", "works_count", "cited_by_count", "geo",
                             "display_name_alternatives", "counts_by_year")])
  geo <- if (is.data.frame(d$geo)) d$geo else list()     # an all-null part infers a null type
  data.frame(id = d$id, name = enc2utf8(d$display_name), works = as.numeric(d$works_count),
             cites = as.numeric(d$cited_by_count),
             lat = if (is.null(geo$latitude)) rep(NA_real_, nrow(d)) else as.numeric(geo$latitude),
             lon = if (is.null(geo$longitude)) rep(NA_real_, nrow(d)) else as.numeric(geo$longitude),
             alts = vapply(d$display_name_alternatives, function(v) paste(enc2utf8(as.character(v)), collapse = "\x1f"), ""),
             cby = vapply(d$counts_by_year, function(y) if (!is.data.frame(y) || NROW(y) == 0) 0 else sum(as.numeric(y$works_count)), 0),
             stringsAsFactors = FALSE)
})))
check("arrow rows == source", nrow(ar) == N, nrow(ar))
for (f in names(jl)) {
  nd <- if (is.numeric(jl[[f]])) sum(!((is.na(jl[[f]]) & is.na(ar[[f]])) |
          (!is.na(jl[[f]]) & !is.na(ar[[f]]) & jl[[f]] == ar[[f]]))) else sum(jl[[f]] != ar[[f]])
  check(paste0("arrow == jsonlite, bit for bit: ", f), identical(jl[[f]], ar[[f]]), nd, "differ")
}
rec("records with coordinates", sum(!is.na(jl$lat)))
rec("sum works_count", sprintf("%.0f", sum(jl$works, na.rm = TRUE)))
rec("sum cited_by_count", sprintf("%.0f", sum(jl$cites, na.rm = TRUE)))
rec("non-ASCII display names", sum(grepl("[^\x01-\x7f]", jl$name, useBytes = TRUE)))
rec("duplicated ids (recorded)", sum(duplicated(jl$id)))

# ---- the user path: stream_in the whole entity into one data.frame, and back out --------------
df <- phase("stream_in -> data.frame", stream_in(file(all_f), verbose = FALSE))
rec("stream_in MB/s", sprintf("%.2f", nbytes / 1e6 / LAST))
check("stream_in rows == source", nrow(df) == N, nrow(df))
check("stream_in ids == source order", identical(df$id, first_id), "")
rec("data.frame columns", ncol(df))
dff <- file.path(WORK, "df.ndjson")
invisible(phase("stream_out data.frame", stream_out(df, file(dff), digits = I(17), always_decimal = TRUE, verbose = FALSE)))
df2 <- phase("stream_in again", stream_in(file(dff), verbose = FALSE))
cols <- vapply(names(df), function(n) identical(df[[n]], df2[[n]]), NA)
rec("data.frame round trip: identical()", identical(df, df2))
rec("data.frame round trip: columns not identical", sum(!cols), "of", length(cols),
    paste(names(cols)[!cols], collapse = ";"))
ndiff <- function(a, b) if (is.list(a) && !is.data.frame(a)) sum(!mapply(identical, a, b)) else
  sum(!((is.na(a) & is.na(b)) | (!is.na(a) & !is.na(b) & a == b)))
tryCatch(for (n in names(cols)[!cols]) {      # a diagnostic must never fail the run
  a <- df[[n]]; b <- df2[[n]]
  if (is.data.frame(a)) for (s in union(names(a), names(b))) {
    if (!identical(a[[s]], b[[s]])) rec(paste0("  ", n, "$", s, " differs (recorded)"),
      if (is.null(a[[s]]) || is.null(b[[s]])) "column absent on one side" else
      c(class(a[[s]])[1], "->", class(b[[s]])[1], ndiff(a[[s]], b[[s]]), "rows"))
  } else rec(paste0("  ", n, " differs (recorded)"), class(a)[1], "->", class(b)[1], ndiff(a, b), "rows",
             "first id", df$id[which(!mapply(identical, a, b))[1]])
}, error = function(e) rec("data.frame diff diagnostic failed (recorded)", conditionMessage(e)))

hwm <- grep("^VmHWM", readLines("/proc/self/status"), value = TRUE)
rec("parent peak RSS (VmHWM)", sub("^VmHWM:\\s*", "", hwm))
if (length(fails)) { cat("FAILED:", paste(fails, collapse = "; "), "\n"); quit(status = 1) }
cat("JSONLITE REAL OK\n")
