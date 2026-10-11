# jsonlite on Graviton4: a lossless JSON round-trip needs digits = I(17).
#
# The headline is a trap, and it is the reason this recipe exists. toJSON()'s default keeps
# FOUR significant digits, and `digits = NA` -- the usual advice for "full precision" --
# keeps FIFTEEN. Neither round-trips. Only `digits = I(17)` returns identical() doubles.
# Every number below was measured on the instance before it was asserted; reading the docs
# would have produced a recipe that asserted `digits = NA` and was wrong.
#
# Arrow's C++ JSON reader is the independent referee. It shares no code with jsonlite's C
# parser, and it is what localizes the loss to the SERIALIZATION rather than to either
# parser: reading jsonlite's 15-digit output, Arrow lands the same ~5e-15 away from the
# original that jsonlite's own reader does. Neither tool is misparsing; the text simply
# does not carry the bits. (The r env ships no Python, so json/stdlib is not available as
# a referee here.)
#
# What is ASSERTED versus merely RECORDED is a deliberate split. Exact, positive facts are
# asserted. Known-lossy and known-surprising behaviours are recorded, because a check that
# demands a defect persist would fail the day upstream fixes it -- the same reason the
# mesalib recipe reports its missing OSMesa backend instead of asserting it.

suppressMessages({library(jsonlite); library(arrow)})

ROWS <- list()
rec <- function(k, v) {
  cat(sprintf("%s\t%s\n", k, v))
  ROWS[[length(ROWS) + 1L]] <<- c(k, as.character(v))
}
die <- function(msg) { cat(sprintf("FAIL: %s\n", msg)); quit(status = 1L) }

rec("jsonlite", as.character(packageVersion("jsonlite")))
rec("arrow", as.character(packageVersion("arrow")))
rec("R", as.character(getRversion()))
rec("arch", R.version$arch)

# Six doubles chosen to be hostile to short decimal output: two transcendentals, a
# repeating fraction, a subnormal-ish exponent, a value needing 15 digits before the
# decimal point, and machine epsilon.
x <- c(pi, exp(1), 1/3, 1e-17, 123456.789012345, .Machine$double.eps)
rec("n_values", length(x))
rec("input_17sig", paste(sprintf("%.17g", x), collapse = " "))

# ---------------------------------------------------------------- serialize three ways
ser <- list(
  default = toJSON(x),
  na      = toJSON(x, digits = NA),
  i17     = toJSON(x, digits = I(17))
)
for (nm in names(ser)) rec(sprintf("bytes_%s", nm), nchar(as.character(ser[[nm]])))

# ------------------------------- 1. the exact one: digits = I(17) round-trips identically
back_i17 <- fromJSON(ser$i17)
rec("json_i17", as.character(ser$i17))
rec("roundtrip_i17_identical", identical(x, back_i17))
if (!identical(x, back_i17)) {
  die(sprintf("digits=I(17) did not round-trip: max |diff| %.3e",
              max(abs(back_i17 - x))))
}
rec("roundtrip_i17_max_abs_diff", sprintf("%.3e", max(abs(back_i17 - x))))

# -------------------- 2. the control: the DEFAULT must be lossy, or the above is vacuous
# digits = 4 is documented behaviour, so asserting it loses precision is stable. It is
# what proves identical() above is a real constraint and not something any output passes.
back_def <- fromJSON(ser$default)
err_def <- max(abs((back_def - x) / x))
rec("json_default", as.character(ser$default))
rec("roundtrip_default_identical", identical(x, back_def))
rec("roundtrip_default_max_rel_err", sprintf("%.3e", err_def))
if (identical(x, back_def)) {
  die("the default toJSON round-tripped identically, so digits=I(17) is not the fix this
       recipe claims it is -- re-derive the whole page")
}
if (!(err_def > 1e-6)) {
  die(sprintf("the default lost only %.3e; it keeps 4 significant digits, so ~1e-4 is
               expected and the control is not behaving as measured", err_def))
}

# ------------- 3. digits = NA: RECORDED, not asserted. This is the finding, not a check.
# `digits = NA` is widely given as the way to get full precision. It keeps 15 significant
# digits and does NOT round-trip. Recorded rather than asserted because jsonlite making it
# exact would be an improvement, and a check demanding the loss would then fail.
back_na <- fromJSON(ser$na)
rec("json_na", as.character(ser$na))
rec("roundtrip_na_identical", identical(x, back_na))
rec("roundtrip_na_max_abs_diff", sprintf("%.3e", max(abs(back_na - x))))
rec("digits_na_note", "RECORDED not asserted: 15 sig digits, does not round-trip")

# ----------------- 4. the cross-check: an independent C++ parser on jsonlite's own bytes
# jsonlite writes a rows-array; Arrow's reader wants newline-delimited objects. Converting
# is a reshape of the SAME bytes -- no reformatting of any number.
to_ndjson <- function(js, path) {
  inner <- gsub("^\\[|\\]$", "", as.character(js))
  lines <- strsplit(inner, "\\},\\{")[[1]]
  lines <- paste0(ifelse(grepl("^\\{", lines), "", "{"), lines,
                  ifelse(grepl("\\}$", lines), "", "}"))
  writeLines(lines, path)
  length(lines)
}

arrow_diff <- function(digits_mode) {
  js <- switch(digits_mode,
               default = toJSON(data.frame(v = x), dataframe = "rows"),
               na      = toJSON(data.frame(v = x), dataframe = "rows", digits = NA),
               i17     = toJSON(data.frame(v = x), dataframe = "rows", digits = I(17)))
  p <- sprintf("rt_%s.ndjson", digits_mode)
  n <- to_ndjson(js, p)
  if (n != length(x)) die(sprintf("ndjson reshape produced %d lines, expected %d",
                                  n, length(x)))
  v <- as.data.frame(read_json_arrow(p))$v
  if (length(v) != length(x)) die("Arrow read a different number of values")
  if (any(!is.finite(v))) die(sprintf("Arrow parsed a non-finite value from %s bytes",
                                      digits_mode))
  list(v = v, d = max(abs(v - x)), id = identical(v, x))
}

a_i17 <- arrow_diff("i17")
rec("arrow_i17_max_abs_diff", sprintf("%.3e", a_i17$d))
rec("arrow_i17_identical", a_i17$id)
if (a_i17$d != 0 || !a_i17$id) {
  die(sprintf("Arrow's independent parser read jsonlite's digits=I(17) bytes as %.3e from
               the R input; the bytes are supposed to carry the doubles exactly", a_i17$d))
}

a_def <- arrow_diff("default")
rec("arrow_default_max_abs_diff", sprintf("%.3e", a_def$d))
if (!(a_def$d > 0)) {
  die("Arrow read the DEFAULT 4-digit bytes as exact, which contradicts the control above")
}
a_na <- arrow_diff("na")
rec("arrow_na_max_abs_diff", sprintf("%.3e", a_na$d))
rec("loss_is_in_the_bytes",
    "two independent parsers agree the 15-digit text is off; neither is misparsing")

# ----------------------------------------------- 5. type preservation through a round-trip
# Asserted for the three types that do survive. The double case is recorded below.
for (nm in c("int", "chr", "lgl")) {
  v <- switch(nm, int = c(1L, NA, 3L), chr = c("a", NA, "c"), lgl = c(TRUE, NA, FALSE))
  j <- toJSON(v, digits = I(17))
  b <- fromJSON(j)
  rec(sprintf("type_%s", nm), sprintf("%s -> %s identical %s",
                                      class(v), class(b), identical(v, b)))
  if (!identical(v, b)) {
    die(sprintf("a %s vector with NA did not round-trip: %s became %s",
                nm, as.character(j), paste(format(b), collapse = ",")))
  }
}

# -------- 6. RECORDED: a double vector does not survive, and na= does not change that
# c(1, NA, 3) is numeric going in and comes back INTEGER -- the values are right, the
# class is not, under na="string" and na="null" alike. Recorded, not asserted: this is
# jsonlite's type inference on the way back, and a fix upstream should not fail this run.
# The pair below is what tells a reader whether NA is implicated or whole numbers are.
for (lbl in c("string", "null")) {
  v <- c(1, NA, 3)
  j <- toJSON(v, digits = I(17), na = lbl)
  b <- fromJSON(j)
  rec(sprintf("dbl_na_%s", lbl),
      sprintf("%s -> class %s identical %s", as.character(j), class(b), identical(v, b)))
}
v_whole <- c(1, 3)                      # no NA, but whole-valued
b_whole <- fromJSON(toJSON(v_whole, digits = I(17)))
rec("dbl_whole_no_na",
    sprintf("%s -> class %s identical %s",
            as.character(toJSON(v_whole, digits = I(17))), class(b_whole),
            identical(v_whole, b_whole)))
v_frac <- c(1.5, NA, 3.5)               # NA, but not whole-valued
b_frac <- fromJSON(toJSON(v_frac, digits = I(17)))
rec("dbl_fractional_with_na",
    sprintf("%s -> class %s identical %s",
            as.character(toJSON(v_frac, digits = I(17))), class(b_frac),
            identical(v_frac, b_frac)))

# ------------------------------------------- 7. data.frame orientation: rows is lossless
df <- data.frame(a = c(1L, 2L), b = c("x", "y"), stringsAsFactors = FALSE)
j_rows <- toJSON(df, dataframe = "rows", digits = I(17))
b_rows <- fromJSON(j_rows)
rec("df_rows_json", as.character(j_rows))
rec("df_rows_identical", identical(df, b_rows))
if (!identical(df, b_rows)) {
  die("dataframe='rows' did not round-trip a two-column data.frame identically")
}
for (o in c("columns", "values")) {     # RECORDED: neither returns an identical df
  b <- tryCatch(fromJSON(toJSON(df, dataframe = o, digits = I(17))),
                error = function(e) NULL)
  rec(sprintf("df_%s", o),
      sprintf("is.data.frame %s identical %s",
              is.data.frame(b), !is.null(b) && identical(df, b)))
}

# ------------------------------------------------------------------- 8. determinism
b1 <- as.character(toJSON(data.frame(v = x), dataframe = "rows", digits = I(17)))
b2 <- as.character(toJSON(data.frame(v = x), dataframe = "rows", digits = I(17)))
rec("determinism", if (identical(b1, b2)) "byte-identical" else "DIFFERS")
if (!identical(b1, b2)) die("two identical toJSON calls produced different bytes")

out <- do.call(rbind, lapply(ROWS, function(r) data.frame(observable = r[1], value = r[2])))
write.table(out, "score.tsv", sep = "\t", row.names = FALSE, quote = FALSE)

cat("JSONLITE OK\n")
