# What decides preprocessCore's pthread_create() result: its thread stack is a fixed
# PTHREAD_STACK_MIN + one page (qnorm.c:530; no environment variable sets it), and glibc
# refuses (EINVAL) a stack too small to hold the process's static TLS. So list every
# mapped ELF's PT_TLS segment, read straight from the ELF program headers.
suppressMessages(library(preprocessCore))
u <- function(b) sum(as.numeric(b) * 256^(seq_along(b) - 1))
tls <- function(f) {
  con <- file(f, "rb"); on.exit(close(con))
  h <- readBin(con, "raw", 64)
  if (length(h) < 64 || !identical(h[1:4], as.raw(c(0x7f, 0x45, 0x4c, 0x46)))) return(c(NA, NA))
  phoff <- u(h[33:40]); phentsize <- u(h[55:56]); phnum <- u(h[57:58])
  seek(con, phoff); ph <- readBin(con, "raw", phentsize * phnum)
  for (i in seq_len(phnum)) {
    o <- (i - 1) * phentsize
    if (u(ph[o + 1:4]) == 7) return(c(u(ph[o + 41:48]), u(ph[o + 49:56])))
  }
  c(0, 0)
}
m <- readLines("/proc/self/maps")
p <- unique(sub("^.* (/.*)$", "\\1", grep(" /", m, value = TRUE)))
p <- p[file.exists(p)]
tot <- 0
for (f in p) {
  t <- tryCatch(tls(f), error = function(e) c(NA, NA))
  if (!is.na(t[1]) && t[1] > 0) {
    cat(sprintf("PT_TLS %8.0f bytes  align %4.0f  %s\n", t[1], t[2], f)); tot <- tot + t[1]
  }
}
cat(sprintf("PT_TLS total over mapped ELFs: %.0f bytes\n", tot))
cat("blas/lapack mapped:", paste(basename(grep("blas|lapack", p, value = TRUE)), collapse = " "), "\n")
cat("R", paste(R.version$major, R.version$minor, sep = "."), " preprocessCore",
    as.character(packageVersion("preprocessCore")), "\n")
cat("libc:", paste(grep("/libc[.-][^/]*so", p, value = TRUE), collapse = " "), "\n")
