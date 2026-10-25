set -uo pipefail
cd /work
STATUS="OK"; REASON=""; COMPUTE=""
nvidia-smi -L > gpus.txt 2>&1 || { STATUS="FAIL"; REASON="no-gpu-in-container"; }
if [ "$STATUS" = OK ]; then
  printf '#include "oplsaa.ff/forcefield.itp"\n#include "oplsaa.ff/spc.itp"\n[ system ]\nwater\n[ molecules ]\n' > topol.top
  gmx solvate -cs spc216 -box 6 6 6 -o box.gro -p topol.top >solv.log 2>&1 || { STATUS=FAIL; REASON="solvate"; }
fi
if [ "$STATUS" = OK ]; then
  printf 'integrator=md\nnsteps=50000\ndt=0.002\ncutoff-scheme=Verlet\ncoulombtype=PME\nrcoulomb=1.0\nrvdw=1.0\ntcoupl=V-rescale\ntc-grps=System\ntau-t=0.1\nref-t=300\nconstraints=h-bonds\n' > md.mdp
  gmx grompp -f md.mdp -c box.gro -p topol.top -o bench.tpr -maxwarn 5 >grompp.log 2>&1 || { STATUS=FAIL; REASON="grompp"; }
fi
NAT=$(grep -oiE "[0-9]+ atoms" grompp.log 2>/dev/null | head -1)
if [ "$STATUS" = OK ]; then
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -l 1 > gpu_util.csv 2>/dev/null &
  SMI=$!
  t0=$(date +%s.%N); gmx mdrun -s bench.tpr -nb gpu -ntmpi 1 -ntomp 8 -g md.log >mdrun.log 2>&1 || { STATUS=FAIL; REASON="mdrun"; }; t1=$(date +%s.%N)
  kill $SMI 2>/dev/null || true
  COMPUTE=$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f",b-a}')
fi
GP=$(grep -icE "using .*GPU|1 GPU|compatible GPUs|Nonbonded.*GPU|will be executed on.* GPU" md.log 2>/dev/null || echo 0)
UMAX=$(awk -F, '{if($1+0>m)m=$1+0}END{print m+0}' gpu_util.csv 2>/dev/null)
UMEAN=$(awk -F, '{s+=$1;n++}END{if(n)printf "%.1f",s/n;else print "n/a"}' gpu_util.csv 2>/dev/null)
NS=$(wc -l </work/gpu_util.csv 2>/dev/null | tr -d ' ')
{ echo "status: $STATUS  reason: $REASON"
  echo "gpu: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  echo "gpus_in_container: $(cat gpus.txt 2>/dev/null)"
  echo "mechanism: spawn launch + docker run --gpus all (NOT the recipe path; task-run GPU gap = spawn#601)"
  echo "run: RUN1 -nb gpu -ntomp 8 (PME on CPU, no offload flags)"
  echo "system_atoms: $NAT  gpu_path_loglines: $GP (>=1 => GPU)  compute_only_wall_s: ${COMPUTE:-n/a}"
  echo "gpu_util_pct: max=${UMAX:-n/a} mean=${UMEAN:-n/a} over ${NS:-0} samples"
  echo "--- gpu_util.csv (time series, 1Hz: util%,mem_MB) ---"; cat gpu_util.csv 2>/dev/null
  echo "--- mdrun.log tail ---"; tail -30 mdrun.log 2>/dev/null
  echo "--- grompp.log tail ---"; tail -8 grompp.log 2>/dev/null
  echo "--- solv.log tail ---"; tail -6 solv.log 2>/dev/null
} > smoke-check.txt
echo "CANARY DONE status=$STATUS reason=$REASON"
