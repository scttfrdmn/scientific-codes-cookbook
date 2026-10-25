#!/bin/bash
# Amber firm-up: (A) cold start, (B) warm start [JIT-cache effect = what an AMI would bake],
# (C) long run for a solid utilization plateau. Native on DLAMI host.
set -uo pipefail
W=${W:-/amberwork}; cd "$W"
GPUNAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
HBM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
if ! ldconfig -p 2>/dev/null | grep -q 'libgfortran\.so\.5'; then sudo dnf install -y libgfortran >/dev/null 2>&1; fi
PM="$W/pmemd26/bin/pmemd.cuda_SPFP"
export AMBERHOME="$W/pmemd26"
CUDALIBS=$(ls -d /usr/local/cuda-12.8/targets/*/lib /usr/local/cuda-12.8/lib64 /usr/local/cuda/lib64 2>/dev/null | tr '\n' ':')
export LD_LIBRARY_PATH="$W/pmemd26/lib:${CUDALIBS}${LD_LIBRARY_PATH:-}"
STATUS=OK
[ -x "$PM" ] || STATUS=FAIL
MISS=$(ldd "$PM" 2>/dev/null | awk '/not found/{print $1}' | sort -u | tr '\n' ' ')
[ -n "$MISS" ] && STATUS=FAIL

mkmdin() { sed -E "s/nstlim=[0-9]+/nstlim=$1/; s/ntwr=[0-9]+/ntwr=$1/" md.in > md_$1.in; }

# phase runner: $1=steps $2=tag -> echoes metrics; also records time-to-first-GPU-activity
phase() {
  steps=$1; tag=$2; mkmdin "$steps"
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -l 1 > util_$tag.csv 2>/dev/null & SMI=$!
  t0=$(date +%s.%N)
  timeout 1200 "$PM" -O -i md_$steps.in -p sys.prmtop -c sys.rst7 -o mdout_$tag -r restrt_$tag >pmemd_$tag.log 2>&1; rc=$?
  t1=$(date +%s.%N); kill $SMI 2>/dev/null || true
  wall=$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f",b-a}')
  nsday=$(grep -i 'ns/day' mdout_$tag 2>/dev/null | tail -1 | sed -E 's/.*ns\/day *= *([0-9.]+).*/\1/')
  amberwall=$(grep -iE 'Total wall time' mdout_$tag 2>/dev/null | sed -E 's/[^0-9]*([0-9]+) *seconds.*/\1/')
  # time-to-first-GPU-activity = count of leading zero samples (1Hz) => startup seconds
  startup=$(awk -F, '{if($1+0>0){print NR-1; exit}}' util_$tag.csv 2>/dev/null); startup=${startup:-NA}
  act=$(awk -F, '$1+0>0{c++; s+=$1}END{if(c)printf "%d samples, mean %.0f%%", c, s/c; else print "none"}' util_$tag.csv)
  pk=$(awk -F, '{if($1+0>m)m=$1+0}END{print m+0}' util_$tag.csv)
  mem=$(awk -F, '{if($2+0>m)m=$2+0}END{print m+0}' util_$tag.csv)
  echo "[$tag] steps=$steps rc=$rc measured_wall_s=$wall amber_wall_s=${amberwall:-NA} ns_per_day=${nsday:-NA}"
  echo "      startup_s(to first GPU activity)=${startup}  active=[$act]  peak=${pk}%  peak_mem_MB=$mem"
}

{
  echo "AMBER FIRM-UP on ${GPUNAME:-NA} (hbm_total_MB=${HBM:-NA})  status=$STATUS ${MISS:+missing:$MISS}"
  echo "purpose: cold-vs-warm startup (CUDA JIT cache = what an AMI would bake) + long-run plateau"
  if [ "$STATUS" = OK ]; then
    ls -la ~/.nv/ComputeCache 2>/dev/null | head -3 || echo "(no JIT cache yet — cold)"
    phase 25000 coldA
    echo "--- JIT cache after first run ---"; du -sh ~/.nv/ComputeCache 2>/dev/null || echo "(none)"
    phase 25000 warmB
    phase 250000 longC
    echo "--- full util series, long run (util%,mem_MB @1Hz) ---"; cat util_longC.csv 2>/dev/null
    echo "--- amber timing (long) ---"; grep -iE 'ns/day|seconds/ns|Total wall' mdout_longC 2>/dev/null | tail -4
  fi
  echo "--- disk ---"; df -h / | tail -1
} > "$W/smoke-check.txt" 2>&1
echo "FIRM-UP DONE status=$STATUS"
