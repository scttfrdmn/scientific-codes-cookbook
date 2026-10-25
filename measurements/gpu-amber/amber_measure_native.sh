#!/bin/bash
# Amber pmemd.cuda measurement — runs NATIVELY on the DLAMI host (no container, no 20GB image pull).
# The DLAMI already has the NVIDIA driver + CUDA 12.8 runtime libs; only nvcc was missing (build-only need).
set -uo pipefail
W=${W:-/amberwork}
cd "$W"
STATUS=OK; REASON=""
GPUNAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
HBM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
nvidia-smi -L >"$W/gpus.txt" 2>&1 || { STATUS=FAIL; REASON=no-gpu; }

# Fortran runtime: build-time dep, not in the base AMI. AL2023 package is libgfortran.
if ! ldconfig -p 2>/dev/null | grep -q 'libgfortran\.so\.5'; then
  sudo dnf install -y libgfortran >/dev/null 2>&1 && echo "installed libgfortran" || echo "WARN libgfortran install failed"
fi
PM="$W/pmemd26/bin/pmemd.cuda_SPFP"
[ -x "$PM" ] || { STATUS=FAIL; REASON=no-pmemd-cuda-binary; }
# Absolute RPATHs baked at build time don't survive relocation; point at the shipped libs + host CUDA runtime.
export AMBERHOME="$W/pmemd26"
CUDALIBS=$(ls -d /usr/local/cuda-12.8/targets/*/lib /usr/local/cuda-12.8/lib64 /usr/local/cuda/lib64 2>/dev/null | tr '\n' ':')
export LD_LIBRARY_PATH="$W/pmemd26/lib:${CUDALIBS}${LD_LIBRARY_PATH:-}"
MISSINGLIBS=$(ldd "$PM" 2>/dev/null | awk '/not found/{print $1}' | sort -u | tr '\n' ' ')
[ -n "$MISSINGLIBS" ] && { STATUS=FAIL; REASON="missing-libs: $MISSINGLIBS"; }

WALL=""
if [ "$STATUS" = OK ]; then
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -l 1 > "$W/util.csv" 2>/dev/null &
  SMI=$!
  t0=$(date +%s.%N)
  timeout 900 "$PM" -O -i md.in -p sys.prmtop -c sys.rst7 -o mdout -r restrt >"$W/pmemd.log" 2>&1 || { STATUS=FAIL; REASON=pmemd-run; }
  t1=$(date +%s.%N)
  kill $SMI 2>/dev/null || true
  WALL=$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f",b-a}')
fi
NSDAY=$(grep -i 'ns/day' "$W/mdout" 2>/dev/null | tail -1 | sed -E 's/.*ns\/day *= *([0-9.]+).*/\1/')
NATOM=$(grep -iE 'NATOM *=' "$W/mdout" 2>/dev/null | head -1 | sed -E 's/.*NATOM *= *([0-9]+).*/\1/')
UMEAN=$(awk -F, '{s+=$1;n++}END{if(n)printf "%.1f",s/n}' "$W/util.csv" 2>/dev/null)
UMAX=$(awk -F, '{if($1+0>m)m=$1+0}END{print m+0}' "$W/util.csv" 2>/dev/null)
MEM=$(awk -F, '{if($2+0>m)m=$2+0}END{print m+0}' "$W/util.csv" 2>/dev/null)
NS=$(wc -l <"$W/util.csv" 2>/dev/null | tr -d ' ')
HBMPCT=$(awk -v m="${MEM:-0}" -v t="${HBM:-0}" 'BEGIN{if(t>0)printf "%.2f",100*m/t; else print "NA"}')
{ echo "status: $STATUS  reason: $REASON"
  echo "code: Amber26 pmemd.cuda_SPFP (LICENSED — built once from Scott's copy; private bucket; NOT redistributable)"
  echo "mechanism: spawn launch + NATIVE host run on DLAMI (no container — avoids a 20GB CUDA-devel image pull)"
  echo "gpu: ${GPUNAME:-NA}  hbm_total_MB: ${HBM:-NA}"
  echo "system: ${NATOM:-NA} atoms (tleap TIP3P ~6.1nm box), 25000 steps, dt=2fs, PME, cut=10A, SHAKE, Langevin 300K"
  echo "ns_per_day: ${NSDAY:-NA}   compute_wall_s: ${WALL:-NA}"
  echo "gpu_compute_util_pct: mean=${UMEAN:-NA} max=${UMAX:-NA} over ${NS:-0} samples"
  echo "gpu_mem_used_MB: ${MEM:-NA} of ${HBM:-NA} => HBM_util_pct: ${HBMPCT}"
  echo "--- util time series (util%,mem_MB @1Hz) ---"; cat "$W/util.csv" 2>/dev/null
  echo "--- mdout timing ---"; grep -iE 'ns/day|seconds/ns|Total wall|Master Total' "$W/mdout" 2>/dev/null | tail -6
  echo "--- pmemd.log tail ---"; tail -15 "$W/pmemd.log" 2>/dev/null
} > "$W/smoke-check.txt"
echo "AMBER NATIVE DONE status=$STATUS ns/day=${NSDAY:-NA}"
