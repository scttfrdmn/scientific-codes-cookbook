# r-arrow against Apache's own committed expected contents for Parquet.
#
# parquet-testing ships four files with a companion *_expect.csv giving every cell, and those
# references were produced by parquet-mr -- an independent JAVA implementation -- so this is a
# genuine cross-implementation check and not Arrow agreeing with itself. That matters here:
# every env in this catalog carrying pyarrow ships the SAME libarrow 25.0.0, so an
# R-writes/Python-reads check would test the binding layers rather than the format.
#
# The reference is version-matched: parquet-testing at the submodule commit arrow 25.0.0 pins.
#
# TWO THINGS THE PROBE SETTLED, both of which change what can be asserted:
#
#  1. INT64_MIN is not representable in R. bit64 reserves -9223372036854775808 as its NA
#     sentinel, so the one cell holding that value reads as NA. Arrow decoded it correctly and R
#     cannot hold it. Asserted as a CHARACTERISED exception -- every mismatch must be exactly a
#     cell whose expected value is INT64_MIN -- rather than hidden behind a tolerance.
#  2. r-arrow does not verify page checksums. Upstream ships deliberately corrupt-CRC files as a
#     negative control, and r-arrow reads them without complaint, because
#     ParquetReaderProperties exposes only the thrift size limits -- there is no
#     page_checksum_verification, though Arrow C++ has had one since 13.0. So those files are
#     REPORTED, not asserted, and the negative control below is constructed instead.
options(warn = 1)
# Without this, int64 downcasts to double and 6374628540732951412 is silently lost -- the
# comparison would then be a double comparison wearing an exact-match label.
options(arrow.int64_downcast = FALSE)

out <- list()
rec <- function(k, v) { out[[k]] <<- v; cat(sprintf("%s\t%s\n", k, v)) }
dump <- function() {
  writeLines(c("observable\tvalue",
               vapply(names(out), function(k) sprintf("%s\t%s", k, out[[k]]), "")),
             "/tmp/score.tsv")
}
die <- function(msg) { dump(); cat(sprintf("FAIL: %s\n", msg)); quit(status = 1) }

suppressPackageStartupMessages({library(arrow); library(bit64)})
INT64_MIN <- "-9223372036854775808"
rec("arrow", as.character(packageVersion("arrow")))
rec("bit64", as.character(packageVersion("bit64")))
ai <- arrow::arrow_info()
rec("libarrow", as.character(ai$build_info$cpp_version))
caps <- names(which(unlist(ai$capabilities)))
rec("codecs", paste(caps, collapse = " "))
for (need in c("parquet", "snappy", "gzip", "zstd")) {
  if (!(need %in% caps)) die(sprintf("libarrow was built without %s", need))
}
rec("identity_capabilities", "parquet, snappy, gzip and zstd all compiled in")

as_chr <- function(x) {
  r <- if (inherits(x, "integer64")) bit64::as.character.integer64(x) else as.character(x)
  r[is.na(r)] <- ""
  r
}

# Compare by POSITION, not by name: the probe measured that 2 of the 4 reference files have
# schema names differing from their CSV header (parquet-mr wrote trailing colons, and one CSV
# header carries a stray leading space). Matching on names would drop those files silently.
compare <- function(t, e) {
  stopifnot(nrow(t) == nrow(e), ncol(t) == ncol(e))
  bad <- 0; excused <- 0; first <- ""
  for (j in seq_len(ncol(t))) {
    a <- as_chr(t[[j]]); b <- e[[j]]
    d <- which(a != b)
    for (i in d) {
      if (b[i] == INT64_MIN && a[i] == "") { excused <- excused + 1; next }
      bad <- bad + 1
      if (first == "") first <- sprintf("col %d row %d: got '%s' want '%s'", j, i, a[i], b[i])
    }
  }
  list(cells = nrow(t) * ncol(t), bad = bad, excused = excused, first = first)
}
read_expect <- function(p) read.csv(p, colClasses = "character", check.names = FALSE,
                                    na.strings = character(0))

pairs <- list(
  c("delta_binary_packed.parquet",            "delta_binary_packed_expect.csv"),
  c("delta_byte_array.parquet",               "delta_byte_array_expect.csv"),
  c("delta_encoding_required_column.parquet", "delta_encoding_required_column_expect.csv"),
  c("delta_encoding_optional_column.parquet", "delta_encoding_optional_column_expect.csv"))

total_cells <- 0; total_bad <- 0; total_excused <- 0; saw_int64 <- FALSE
for (p in pairs) {
  t <- arrow::read_parquet(file.path("/tmp", p[1]))
  e <- read_expect(file.path("/tmp", p[2]))
  if (nrow(t) != nrow(e) || ncol(t) != ncol(e))
    die(sprintf("%s is %dx%d, its committed CSV is %dx%d", p[1], nrow(t), ncol(t),
                nrow(e), ncol(e)))
  if (any(vapply(t, function(c) inherits(c, "integer64"), TRUE))) saw_int64 <- TRUE
  r <- compare(t, e)
  total_cells <- total_cells + r$cells
  total_bad <- total_bad + r$bad
  total_excused <- total_excused + r$excused
  rec(sprintf("%s", p[1]),
      sprintf("%d x %d, %d cells, %d unexplained, %d INT64_MIN",
              nrow(t), ncol(t), r$cells, r$bad, r$excused))
  if (r$bad) rec(sprintf("%s_first_mismatch", p[1]), r$first)
}
# If int64 silently downcast to double, every comparison above would still have "passed" on the
# narrow columns while quietly mangling the wide ones. Assert the type actually arrived.
if (!saw_int64) die("no integer64 column -- arrow.int64_downcast did not take effect")
rec("cells_compared", total_cells)
rec("cells_unexplained", total_bad)
rec("cells_int64_min", total_excused)
if (total_bad) die(sprintf("%d cells differ from Apache's committed values: %s",
                           total_bad, "see first_mismatch above"))
if (total_excused != 1)
  die(sprintf("expected exactly 1 INT64_MIN cell, found %d -- the exception is not what it was",
              total_excused))
rec("identity_committed_values",
    sprintf("%d of %d cells reproduce parquet-mr's committed values exactly; the 1 exception is INT64_MIN, unrepresentable in bit64",
            total_cells - total_excused, total_cells))

# ---- the constructed negative control --------------------------------------------------------
# Upstream's corrupt-CRC files cannot serve as one (see the header), so the comparator is tested
# directly: perturb a single expected cell and it must be caught. Without this, "0 unexplained"
# above is unfalsifiable.
e <- read_expect("/tmp/delta_byte_array_expect.csv")
t <- arrow::read_parquet("/tmp/delta_byte_array.parquet")
e2 <- e; e2[7, 3] <- paste0(e2[7, 3], "X")
r <- compare(t, e2)
rec("perturbed_one_cell_detected", r$bad)
if (r$bad != 1) die(sprintf("perturbing one cell produced %d mismatches -- the comparator is not sensitive", r$bad))
rec("identity_comparator_discriminates", "a single altered cell is detected, so 0 means 0")

# ---- the write path, against the same published reference -------------------------------------
# Round-tripping through each codec and re-comparing to the CSV checks the WRITER transitively:
# anything the writer corrupts shows up as a difference from parquet-mr's values, not merely as
# a difference from what we just wrote.
sizes <- c()
for (codec in c("uncompressed", "snappy", "gzip", "zstd")) {
  f <- sprintf("/tmp/rt_%s.parquet", codec)
  arrow::write_parquet(t, f, compression = codec)
  sizes[codec] <- file.size(f)
  r <- compare(arrow::read_parquet(f), e)
  if (r$bad) die(sprintf("round-trip via %s lost %d cells", codec, r$bad))
}
rec("roundtrip_sizes_bytes", paste(sprintf("%s=%d", names(sizes), sizes), collapse = " "))
if (length(unique(sizes)) != length(sizes))
  die("two codecs produced identical file sizes -- at least one did not run")
if (sizes[["uncompressed"]] <= max(sizes[["snappy"]], sizes[["gzip"]], sizes[["zstd"]]))
  die("a compressed file is not smaller than the uncompressed one")
rec("identity_roundtrip",
    sprintf("all 4 codecs round-trip to parquet-mr's values; sizes are distinct and all compress (%.2fx best)",
            sizes[["uncompressed"]] / min(sizes)))

# ---- the checksum files: reported, because r-arrow cannot verify a page CRC -------------------
good <- c("datapage_v1-uncompressed-checksum.parquet" = 5120,
          "datapage_v1-snappy-compressed-checksum.parquet" = 5120,
          "plain-dict-uncompressed-checksum.parquet" = 1000)
for (f in names(good)) {
  d <- arrow::read_parquet(file.path("/tmp", f))
  if (nrow(d) != good[[f]])
    die(sprintf("%s has %d rows, expected %d", f, nrow(d), good[[f]]))
}
rec("identity_good_crc_files", "3 files with a matching page CRC read to their documented shapes")
props <- ls(arrow::ParquetReaderProperties$create())
rec("reader_properties_members", paste(props, collapse = " "))
rec("page_checksum_api_present", any(grepl("checksum", props)))
corrupt <- c("datapage_v1-corrupt-checksum.parquet", "rle-dict-uncompressed-corrupt-checksum.parquet")
res <- vapply(corrupt, function(f) {
  d <- tryCatch(arrow::read_parquet(file.path("/tmp", f)), error = function(e) e)
  if (inherits(d, "error")) "rejected" else sprintf("read %d rows", nrow(d))
}, "")
rec("corrupt_crc_outcome", paste(sprintf("%s: %s", corrupt, res), collapse = " | "))
rec("observation_no_crc_verification",
    "REPORTED, NOT ASSERTED -- r-arrow exposes no page_checksum_verification, so upstream's corrupt-CRC files read without complaint")

dump()
cat("R-ARROW OK\n")
