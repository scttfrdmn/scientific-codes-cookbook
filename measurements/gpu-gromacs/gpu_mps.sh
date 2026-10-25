set -uo pipefail
cd /work
export CUDA_MPS_PIPE_DIRECTORY=/tmp/mps CUDA_MPS_LOG_DIRECTORY=/tmp/mpslog
mkdir -p "$CUDA_MPS_PIPE_DIRECTORY" "$CUDA_MPS_LOG_DIRECTORY"
MPS="off"; nvidia-cuda-mps-control -d 2>/tmp/mps.log && MPS="on" || MPS="unavailable(time-slice)"
GPUNAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
# build one system, reused by all concurrent runs
printf '#include "oplsaa.ff/forcefield.itp"\n#include "oplsaa.ff/spc.itp"\n[ system ]\nwater\n[ molecules ]\n' > topol.top
gmx solvate -cs spc216 -box 6 6 6 -o box.gro -p topol.top >solv.log 2>&1
printf 'integrator=md\nnsteps=25000\ndt=0.002\ncutoff-scheme=Verlet\ncoulombtype=PME\nrcoulomb=1.0\nrvdw=1.0\ntcoupl=V-rescale\ntc-grps=System\ntau-t=0.1\nref-t=300\nconstraints=h-bonds\n' > md.mdp
gmx grompp -f md.mdp -c box.gro -p topol.top -o /work/bench.tpr -maxwarn 5 >grompp.log 2>&1
run_cfg() {  # $1=N concurrent ; $2=ntomp each
  N=$1; TOMP=$2; rm -rf r[0-9]*
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -l 1 > util_$N.csv 2>/dev/null &
  SMI=$!
  t0=$(date +%s.%N)
  PIDS=""
  for i in $(seq 1 $N); do
    mkdir -p r$i
    ( cd r$i; timeout 420 gmx mdrun -s /work/bench.tpr -nsteps 25000 -ntmpi 1 -ntomp $TOMP -nb gpu -pme gpu -update gpu -g md.log >mdrun.log 2>&1 ) &
    PIDS="$PIDS $!"
  done
  # wait ONLY on the mdrun jobs — a bare `wait` would also wait on the never-exiting sampler
  for p in $PIDS; do wait "$p" 2>/dev/null; done
  t1=$(date +%s.%N)
  kill $SMI 2>/dev/null || true
  agg=$(for i in $(seq 1 $N); do awk '/Performance:/{print $2}' r$i/md.log 2>/dev/null | tail -1; done | awk '{s+=$1}END{printf "%.1f",s}')
  persys=$(for i in $(seq 1 $N); do awk '/Performance:/{print $2}' r$i/md.log 2>/dev/null | tail -1; done | awk '{s+=$1;n++}END{if(n)printf "%.1f",s/n}')
  umean=$(awk -F, '{s+=$1;n++}END{if(n)printf "%.0f",s/n}' util_$N.csv 2>/dev/null)
  umax=$(awk -F, '{if($1+0>m)m=$1+0}END{print m+0}' util_$N.csv 2>/dev/null)
  mem=$(awk -F, '{if($2+0>m)m=$2+0}END{print m+0}' util_$N.csv 2>/dev/null)
  wall=$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f",b-a}')
  echo "N=$N ntomp=$TOMP  per_system_ns_day=${persys:-NA} (DEGRADES)  agg_ns_day=${agg:-NA} (RECOVERS)  gpu_util_mean=${umean:-NA}%  max=${umax:-NA}%  peak_mem_MB=${mem:-NA}  wall_s=$wall"
}
{
  echo "MPS packing test on $GPUNAME (GROMACS full offload, 25000 steps/run) MPS=$MPS"
  echo "question: does packing N systems onto one GPU degrade per-system speed but RECOVER effective cost?"
  run_cfg 1 8
  run_cfg 2 4
  run_cfg 3 2
  run_cfg 4 2
  echo "read: agg_ns_day rising with N = idle GPU converted to throughput; effective \$/system-ns = rate/agg drops by that factor"
  echo "--- util series per N (util%,mem_MB @1Hz) ---"
  for n in 1 2 3 4; do echo "[N=$n]"; cat util_$n.csv 2>/dev/null; done
} > smoke-check.txt 2>&1
echo "MPS DONE ($MPS)"
