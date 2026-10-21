#!/usr/bin/env bash
# measurements/gw-real: one arm of the r1 quantile-miss split (arms.py).
#   arms.sh calibration | arms.sh sampler <naccept>
set +e
set -uo pipefail
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp MPLCONFIGDIR=/tmp/mpl
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
mkdir -p /tmp/out /tmp/work
cd "$(dirname "$0")"      # the staged inputs/gw-real/ prefix: scripts, data/, r1/
{ uname -m; nproc --all; lscpu | grep -E '^(Model name|Thread)'; } > /tmp/out/host.txt
T0=$(date +%s)
case "$1" in
  calibration)
    python3 -u arms.py calibration r1/GW150914_result.hdf5 r1/quantiles.tsv data /tmp/out 48 1000 4000 2>&1 \
      | grep --line-buffered -v 'Warning\|deprecated behaviour' | tee /tmp/out/arms.log ;;
  sampler)
    python3 -u arms.py sampler "$2" data /tmp/out 64 500 2>&1 \
      | grep --line-buffered -v 'Warning\|deprecated behaviour' | tee /tmp/out/pe.log ;;
esac
RC=${PIPESTATUS[0]}
echo "wall_s	$(( $(date +%s) - T0 ))" > /tmp/out/phases.txt
awk '{printf "cgroup_peak_gib\t%.2f\n", $1/2^30}' /sys/fs/cgroup/memory.peak >> /tmp/out/phases.txt
cat /tmp/out/phases.txt
exit $RC
