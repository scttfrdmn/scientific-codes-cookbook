#!/bin/bash
# Amber pmemd.cuda measurement — runs INSIDE nvidia/cuda:12.8.1-devel (same image it was built in,
# so runtime libs match). Host wrapper stages /work with pmemd26/, sys.prmtop, sys.rst7, md.in
# and always uploads /work/smoke-check.txt. No aws CLI in here.
set -uo pipefail
cd /work
STATUS=OK; REASON=""
GPUNAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
HBM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
nvidia-smi -L >/work/gpus.txt 2>&1 || { STATUS=FAIL; REASON=no-gpu-in-container; }
# NOTE: pmemd.cuda is an ABSOLUTE symlink -> /opt/amber/pmemd26/bin/pmemd.cuda_SPFP (the build host's
# path), so it dangles wherever we untar. Call the real SPFP binary directly — SPFP is also Amber's
# recommended production GPU precision model.
PM=/work/pmemd26/bin/pmemd.cuda_SPFP
[ -x "$PM" ] || PM=/work/pmemd26/bin/pmemd.cuda
[ -x "$PM" ] || { STATUS=FAIL; REASON=no-pmemd-cuda-binary; }
# The build bakes ABSOLUTE RPATHs (/opt/amber/pmemd26/lib) so relocating breaks lib lookup
# (libemil.so / libblas / liblapack / libkmmd all ship in the tree). Point at them explicitly.
export AMBERHOME=/work/pmemd26
export LD_LIBRARY_PATH=/work/pmemd26/lib:${LD_LIBRARY_PATH:-}
# A container that BUILDS is not a container that RUNS: gfortran was apt-installed for the build,
# so its runtime (libgfortran.so.5) is absent in the clean run image. Add just the runtime.
if ! ldconfig -p 2>/dev/null | grep -q 'libgfortran\.so\.5'; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq libgfortran5 >/dev/null 2>&1 \
    && echo "installed libgfortran5" || echo "WARN: libgfortran5 install failed"
fi
MISSINGLIBS=$(ldd "$PM" 2>/dev/null | awk '/not found/{print $1}' | sort -u | tr '\n' ' ')
[ -n "$MISSINGLIBS" ] && { STATUS=FAIL; REASON="missing-libs: $MISSINGLIBS"; }
for f in sys.prmtop sys.rst7 md.in; do [ -f /work/$f ] || { STATUS=FAIL; REASON=missing-$f; }; done
WALL=""
if [ "$STATUS" = OK ]; then
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -l 1 > /work/util.csv 2>/dev/null &
  SMI=$!
  t0=$(date +%s.%N)
  timeout 900 "$PM" -O -i md.in -p sys.prmtop -c sys.rst7 -o mdout -r restrt -x mdcrd >/work/pmemd.log 2>&1 || { STATUS=FAIL; REASON=pmemd-run; }
  t1=$(date +%s.%N)
  kill $SMI 2>/dev/null || true
  WALL=$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f",b-a}')
fi
NSDAY=$(grep -i 'ns/day' /work/mdout 2>/dev/null | tail -1 | sed -E 's/.*ns\/day *= *([0-9.]+).*/\1/')
NATOM=$(grep -iE 'NATOM *=' /work/mdout 2>/dev/null | head -1 | sed -E 's/.*NATOM *= *([0-9]+).*/\1/')
UMEAN=$(awk -F, '{s+=$1;n++}END{if(n)printf "%.1f",s/n}' /work/util.csv 2>/dev/null)
UMAX=$(awk -F, '{if($1+0>m)m=$1+0}END{print m+0}' /work/util.csv 2>/dev/null)
MEM=$(awk -F, '{if($2+0>m)m=$2+0}END{print m+0}' /work/util.csv 2>/dev/null)
NS=$(wc -l </work/util.csv 2>/dev/null | tr -d ' ')
HBMPCT=$(awk -v m="${MEM:-0}" -v t="${HBM:-0}" 'BEGIN{if(t>0)printf "%.2f",100*m/t; else print "NA"}')
{ echo "status: $STATUS  reason: $REASON"
  echo "code: Amber26 pmemd.cuda_SPFP (LICENSED — built once from Scott's copy, private bucket; NOT redistributable)"
  echo "binary: $PM"
  echo "mechanism: spawn launch + docker run --privileged --gpus all in nvidia/cuda:12.8.1-devel (build+run same image)"
  echo "gpu: ${GPUNAME:-NA}  hbm_total_MB: ${HBM:-NA}"
  echo "system: ${NATOM:-NA} atoms (tleap TIP3P, ~6.1nm box), 25000 steps, dt=2fs, PME, cut=10A, SHAKE, Langevin 300K"
  echo "ns_per_day: ${NSDAY:-NA}   compute_wall_s: ${WALL:-NA}"
  echo "gpu_compute_util_pct: mean=${UMEAN:-NA} max=${UMAX:-NA} over ${NS:-0} samples"
  echo "gpu_mem_used_MB: ${MEM:-NA} of ${HBM:-NA} => HBM_util_pct: ${HBMPCT}"
  echo "--- util time series (util%,mem_MB @1Hz) ---"; cat /work/util.csv 2>/dev/null
  echo "--- mdout timing ---"; grep -iE 'ns/day|seconds/ns|Total wall|Master Total' /work/mdout 2>/dev/null | tail -6
  echo "--- pmemd.log tail ---"; tail -12 /work/pmemd.log 2>/dev/null
} > /work/smoke-check.txt
echo "AMBER MEASURE DONE status=$STATUS ns/day=${NSDAY:-NA}"
