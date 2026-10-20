#!/usr/bin/env bash
# The plumed-real task.
#   bash run.sh <workers> [reference-colvars.tar.gz]      # the cohort staged at /tmp/cohort.tar
#   bash run.sh probe <workers> <n>                       # fetch n trajectories from RODA first
set +e
set -uo pipefail
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
# One thread per process: the parallelism is one trajectory per worker.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PLUMED_NUM_THREADS=1
export PLUMED_KERNEL="$P/lib/libplumedKernel.so"
cd /tmp
mkdir -p out
if [ "$1" = probe ]; then
  W=$2
  python3 -u fetch.py /tmp/probe-bundle "$3" 2>&1 | tee out/fetch.log
  BUNDLE=/tmp/probe-bundle/cohort.tar; REF=""
else
  W=$1; REF="${2:-}"
  sha256sum -c inputs.sha256 || { echo "FAIL: staged cohort does not match its pin" | tee out/check.txt; exit 1; }
  BUNDLE=/tmp/cohort.tar
fi
{
  echo "uname_m $(uname -m)"; echo "nproc_all $(nproc --all)"
  lscpu | grep -E 'Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)' | sed 's/  */ /g'
  plumed info --long-version 2>&1 | tail -1
  cpptraj --version 2>&1 | tail -1
  ldd "$P/lib/libplumedKernel.so" | grep -iE 'blas|lapack|mkl|libm\.' | sort -u
  ldd --version 2>&1 | head -1
} > out/host.txt
cat out/host.txt
T0=$(date +%s)
# mdtraj's DCD reader prints two dcdplugin lines per file to stderr; keep them out of the record.
python3 -u real.py "$BUNDLE" out "$W" $REF 2>&1 | grep -v '^dcdplugin)' | tee out/check.txt
rc=${PIPESTATUS[0]}
echo "tool_wall_s $(( $(date +%s) - T0 ))" >> out/host.txt
awk '{printf "cgroup_memory_peak_gib %.3f\n", $1/1073741824}' /sys/fs/cgroup/memory.peak >> out/host.txt 2>/dev/null
if [ "$1" = probe ] && [ "$rc" = 0 ]; then
  # Exercise the cross-chip path against this run's own COLVARs: it must find them identical.
  mkdir -p out/self && cp out/colvars.tar.gz /tmp/self-ref.tar.gz
  python3 -u real.py "$BUNDLE" out/self "$W" /tmp/self-ref.tar.gz 2>&1 | grep -v '^dcdplugin)' | tee out/self-check.txt
  rc=${PIPESTATUS[0]}
  rm -f out/self/colvars.tar.gz
fi
cat out/host.txt
exit $rc
