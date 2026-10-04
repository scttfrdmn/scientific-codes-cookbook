# Does the OpenBLAS DYNAMIC_ARCH finding generalise to x86? Two quantities in one run:
#  - a FIXED POINT (converged dominant eigenvalue) -- expected kernel-robust
#  - a PATH (sum over every intermediate norm) -- expected kernel-determined
# Same amd64 image on every x86 rung, so only the host microarchitecture differs.
f <- function(k, v) cat(sprintf("%-26s %s\n", k, v))
f("arch", R.version$arch)
f("blas_lib", basename(La_library()))
f("cpu_model", sub("^model name\\s*:\\s*", "",
    grep("model name|Model name", readLines("/proc/cpuinfo"), value = TRUE)[1]))
# Read the selected kernel through ctypes. Deliberately NOT via R's .Call: these are
# plain C symbols returning char*, not registered R routines, so .Call on them dies.
cat(paste(system(paste0("python3 -c \"",
  "import ctypes,glob;so=sorted(glob.glob('/opt/conda/lib/libopenblas*.so*'));",
  "l=ctypes.CDLL(so[-1])\n",
  "for n in ('openblas_get_corename','openblas_get_config'):\n",
  "    g=getattr(l,n);g.restype=ctypes.c_char_p;print('%-26s %s'%(n,g().decode()))\"" ),
  intern = TRUE), collapse = "\n"), "\n")

set.seed(20261004)
n <- 200
A <- matrix(rnorm(n * n), n, n); A <- A + t(A)      # symmetric: real spectrum
x <- rep(1 / sqrt(n), n)
path <- 0
for (i in 1:20000) {                                 # power iteration through BLAS
  x <- A %*% x
  nx <- sqrt(sum(x * x))
  path <- path + nx                                  # PATH: every intermediate, accumulated
  x <- x / nx
}
f("fixed_point_eigenvalue", sprintf("%.15e", as.numeric(t(x) %*% (A %*% x))))
f("path_sum_of_norms",      sprintf("%.15e", path))
# LAPACK's full eigendecomposition is an independent route to the same number the BLAS
# power iteration converges on (largest MAGNITUDE eigenvalue, which here is negative) --
# so the run carries its own cross-check rather than only a remembered value.
ev  <- eigen(A, only.values = TRUE)$values
ref <- ev[which.max(abs(ev))]
f("lapack_eigen_largest_abs", sprintf("%.15e", ref))
f("power_vs_lapack_reldiff",  sprintf("%.3e", abs(as.numeric(t(x) %*% (A %*% x)) - ref) / abs(ref)))
