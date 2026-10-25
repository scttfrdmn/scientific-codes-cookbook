set -uo pipefail
cd /work
STATUS=OK; REASON=""
nvidia-smi -L > gpus.txt 2>&1 || { STATUS=FAIL; REASON=no-gpu-in-container; }
if [ "$STATUS" = OK ]; then
  printf '#include "oplsaa.ff/forcefield.itp"\n#include "oplsaa.ff/spc.itp"\n[ system ]\nwater\n[ molecules ]\n' > topol.top
  gmx solvate -cs spc216 -box 6 6 6 -o box.gro -p topol.top >solv.log 2>&1 || { STATUS=FAIL; REASON=solvate; }
fi
if [ "$STATUS" = OK ]; then
  printf 'integrator=md\nnsteps=25000\ndt=0.002\ncutoff-scheme=Verlet\ncoulombtype=PME\nrcoulomb=1.0\nrvdw=1.0\ntcoupl=V-rescale\ntc-grps=System\ntau-t=0.1\nref-t=300\nconstraints=h-bonds\n' > md.mdp
  gmx grompp -f md.mdp -c box.gro -p topol.top -o bench.tpr -maxwarn 5 >grompp.log 2>&1 || { STATUS=FAIL; REASON=grompp; }
fi
NAT=$(grep -oiE "[0-9]+ atoms" grompp.log 2>/dev/null | head -1)
if [ "$STATUS" = OK ]; then
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -l 1 > util.csv 2>/dev/null & smi=$!
  timeout 400 gmx mdrun -s bench.tpr -nsteps 25000 -ntmpi 1 -ntomp 8 -nb gpu -pme gpu -update gpu -g md.log >mdrun.log 2>&1 || { STATUS=FAIL; REASON=mdrun; }
  kill $smi 2>/dev/null || true
fi
WALL=$(awk '/^ *Time:/{print $3}' md.log 2>/dev/null | tail -1)
NSDAY=$(awk '/Performance:/{print $2}' md.log 2>/dev/null | tail -1)
UMAX=$(awk -F, '{if($1+0>m)m=$1+0}END{print m+0}' util.csv 2>/dev/null)
UMEAN=$(awk -F, '{s+=$1;n++}END{if(n)printf "%.1f",s/n;else print "na"}' util.csv 2>/dev/null)
NS=$(wc -l <util.csv 2>/dev/null | tr -d ' ')
{ echo "status: $STATUS  reason: $REASON"
  echo "gpu: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  echo "system_atoms: ${NAT:-NA}"
  echo "gpu_run: -nb gpu -pme gpu -update gpu -ntmpi 1 -ntomp 8 (25000 steps, full offload)"
  echo "wall_s: ${WALL:-NA}  ns_per_day: ${NSDAY:-NA}"
  echo "gpu_util_pct: max=${UMAX:-NA} mean=${UMEAN:-NA} over ${NS:-0} samples"
  echo "--- util series (util%,mem_MB @1Hz) ---"; cat util.csv 2>/dev/null
} > smoke-check.txt
echo "GPU MEASURE DONE status=$STATUS ns/day=${NSDAY:-NA}"
