set +e
export PATH=/usr/local/bin:/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
mkdir -p /tmp/out
{
uname -m; grep -m1 'model name' /proc/cpuinfo; grep -m1 'CPU part' /proc/cpuinfo
getconf PTHREAD_STACK_MIN 2>&1 || echo "getconf PTHREAD_STACK_MIN unavailable"
getconf GNU_LIBC_VERSION 2>&1 || true
Rscript /tmp/tls-probe.R 2>&1; echo "tls-probe exit $?"
Q='x <- matrix(runif(4e5), ncol = 4); y <- preprocessCore::normalize.quantiles(x); cat("normalize.quantiles ok", dim(y), "\n")'
echo "== variant: default"; Rscript -e "$Q" 2>&1; echo "exit $?"
echo "== variant: R_THREADS=1"; R_THREADS=1 Rscript -e "$Q" 2>&1; echo "exit $?"
echo "== variant: R_THREADS=4"; R_THREADS=4 Rscript -e "$Q" 2>&1; echo "exit $?"
echo "== variant: GLIBC_TUNABLES=glibc.rtld.nns=1:glibc.rtld.optional_static_tls=0"
GLIBC_TUNABLES=glibc.rtld.nns=1:glibc.rtld.optional_static_tls=0 Rscript -e "$Q" 2>&1; echo "exit $?"
echo "== variant: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1"
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 Rscript -e "$Q" 2>&1; echo "exit $?"
} > /tmp/out/tls-probe.txt 2>&1
cat /tmp/out/tls-probe.txt
