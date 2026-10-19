#!/usr/bin/env bash
# The openmm-mdanalysis-real task: OpenMM on the Amber20 suite's JAC and STMV, then check.py.
#   bash run.sh <threads> <jac steps> <stmv steps> [probe thread list]
set +e
set -uo pipefail
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH TMPDIR=/tmp HOME=/tmp LC_ALL=C
export OPENMM_CPU_THREADS="$1" OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
cd /tmp
mkdir -p out
sha256sum -c inputs.sha256 || { echo "FAIL: staged suite does not match its pin" | tee out/check.txt; exit 1; }
tar -xzf Amber20_Benchmark_Suite.tar.gz PME/Topologies/JAC.prmtop PME/Coordinates/JAC.inpcrd \
  PME/Topologies/STMV.prmtop PME/Coordinates/STMV.inpcrd || { echo "FAIL: extract" | tee out/check.txt; exit 1; }
{
  echo "uname_m $(uname -m)"; echo "nproc $(nproc --all)"
  lscpu | grep -E 'Model name|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node\(s\)|Flags' | sed 's/  */ /g' | cut -c1-400
  ldd "$P"/lib/libOpenMMCPU.so "$P"/lib/plugins/libOpenMMPME*.so 2>/dev/null | grep -iE 'blas|fftw|mkl' | sort -u
  python3 -c 'import numpy, threadpoolctl; [print("blas", d.get("internal_api"), d.get("version"), d.get("architecture")) for d in threadpoolctl.threadpool_info()]' 2>/dev/null
} > out/host.txt
cat out/host.txt
T0=$(date +%s)
python3 -u real.py out "$1" "$2" "$3" "${4:-}" 2>&1 | tee out/openmm.log
rc=${PIPESTATUS[0]}
echo "tool_wall_s $(( $(date +%s) - T0 ))" >> out/host.txt
cat /sys/fs/cgroup/memory.peak 2>/dev/null | awk '{printf "cgroup_memory_peak_gib %.2f\n", $1/1073741824}' >> out/host.txt
[ "$rc" = 0 ] || { echo "FAIL: real.py exited $rc" | tee -a out/check.txt; exit 1; }
if [ -f check.py ]; then
  python3 -u check.py out 2>&1 | tee out/check.txt
  rc=${PIPESTATUS[0]}
fi
cat out/host.txt
exit $rc
