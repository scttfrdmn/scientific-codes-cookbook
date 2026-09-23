set -euo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
cd /tmp
LBL="${LBL:-unknown}"
printf '%s  stream.c\n' a52bae5e175bea3f7832112af9c085adab47117f7d2ce219165379849231692b > s.sha
sha256sum -c s.sha
CC=aarch64-conda-linux-gnu-gcc
$CC -O3 -fopenmp -DSTREAM_ARRAY_SIZE=40000000 -DNTIMES=20 -mcpu=neoverse-512tvb stream.c -o stream -lm
echo "##### FULL STREAM OUTPUT, 1 thread #####"
OMP_NUM_THREADS=1 OMP_PROC_BIND=close OMP_PLACES=cores ./stream 2>&1
echo "##### FULL STREAM OUTPUT, all $(nproc) threads #####"
OMP_NUM_THREADS=$(nproc) OMP_PROC_BIND=close OMP_PLACES=cores ./stream 2>&1
echo "##### how many threads did OpenMP actually use? #####"
cat > t.c <<'EOF'
#include <omp.h>
#include <stdio.h>
int main(void){ int n=0; 
#pragma omp parallel
 { 
#pragma omp master
   n=omp_get_num_threads(); }
 printf("%d\n", n); return 0; }
EOF
$CC -O3 -fopenmp t.c -o t
printf 'omp_threads_at_1\t%s\n'  "$(OMP_NUM_THREADS=1 ./t)"
printf 'omp_threads_at_16\t%s\n' "$(OMP_NUM_THREADS=16 ./t)"
printf 'omp_threads_default\t%s\n' "$(./t)"
