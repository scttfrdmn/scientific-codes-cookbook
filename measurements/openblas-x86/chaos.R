# Take 2. The first probe's "path" was a power iteration, which CONVERGES -- so its sum of
# 20,000 norms is ~20000 x lambda, a fixed point wearing a path's clothes, and it came back
# bit-identical on every kernel. This one is genuinely chaotic: a logistic nonlinearity at
# r = 3.9 applied between BLAS gemv calls, so a rounding difference in gemv amplifies
# exponentially instead of being damped out.
f <- function(k, v) cat(sprintf("%-26s %s\n", k, v))
f("arch", R.version$arch)
f("cpu_model", sub("^model name\\s*:\\s*", "",
    grep("model name|Model name", readLines("/proc/cpuinfo"), value = TRUE)[1]))
cat(paste(system(paste0("python3 -c \"",
  "import ctypes,glob;so=sorted(glob.glob('/opt/conda/lib/libopenblas*.so*'));",
  "l=ctypes.CDLL(so[-1])\n",
  "g=l.openblas_get_corename;g.restype=ctypes.c_char_p;print('%-26s %s'%('openblas_get_corename',g().decode()))\""),
  intern = TRUE), collapse = "\n"), "\n")

set.seed(20261004)
n <- 200
A <- matrix(rnorm(n * n), n, n); A <- A + t(A)
x <- rep(0.5, n); x[1] <- 0.5000001
path <- 0; lyap <- 0
for (i in 1:5000) {
  y  <- A %*% x                      # BLAS gemv: where the kernel choice enters
  y  <- y / sqrt(sum(y * y))
  x  <- as.numeric(3.9 * y * (1 - y))  # logistic map: chaotic, amplifies the last bits
  path <- path + sum(abs(x))
  if (i %% 1000 == 0) lyap <- lyap + log(abs(sum(x)))
}
f("chaotic_path_sum",   sprintf("%.15e", path))
f("chaotic_final_x1",   sprintf("%.15e", x[1]))
f("chaotic_lyap_proxy", sprintf("%.15e", lyap))
