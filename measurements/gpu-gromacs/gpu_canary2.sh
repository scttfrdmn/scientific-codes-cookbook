set -uo pipefail
cd /work
PRE=OK
nvidia-smi -L > gpus.txt 2>&1 || PRE="no-gpu-in-container"
printf '#include "oplsaa.ff/forcefield.itp"\n#include "oplsaa.ff/spc.itp"\n[ system ]\nwater\n[ molecules ]\n' > topol.top
gmx solvate -cs spc216 -box 6 6 6 -o box.gro -p topol.top >solv.log 2>&1 || PRE="solvate"
printf 'integrator=md\nnsteps=25000\ndt=0.002\ncutoff-scheme=Verlet\ncoulombtype=PME\nrcoulomb=1.0\nrvdw=1.0\ntcoupl=V-rescale\ntc-grps=System\ntau-t=0.1\nref-t=300\nconstraints=h-bonds\n' > md.mdp
gmx grompp -f md.mdp -c box.gro -p topol.top -o bench.tpr -maxwarn 5 >grompp.log 2>&1 || PRE="grompp"

run_cfg() {  # $1=tag ; rest=mdrun flags
  tag=$1; shift
  nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits -l 1 > util_$tag.csv 2>/dev/null &
  smi=$!
  timeout 300 gmx mdrun -s bench.tpr -nsteps 25000 -ntmpi 1 -ntomp 8 "$@" -deffnm run_$tag -g run_$tag.log >mdrun_$tag.log 2>&1
  rc=$?
  kill $smi 2>/dev/null || true
  wall=$(awk '/^ *Time:/{print $3}' run_$tag.log 2>/dev/null | tail -1)
  nsday=$(awk '/Performance:/{print $2}' run_$tag.log 2>/dev/null | tail -1)
  umax=$(awk -F, '{if($1+0>m)m=$1+0}END{print m+0}' util_$tag.csv 2>/dev/null)
  umean=$(awk -F, '{s+=$1;n++}END{if(n)printf "%.1f",s/n;else print "na"}' util_$tag.csv 2>/dev/null)
  ns=$(wc -l <util_$tag.csv 2>/dev/null | tr -d ' ')
  err=$(grep -iE 'Fatal error|not supported|cannot' run_$tag.log 2>/dev/null | head -1)
  echo "CFG $tag rc=$rc flags=[$*] wall_s=${wall:-NA} ns_per_day=${nsday:-NA} gpu_util_max=${umax:-NA} mean=${umean:-NA} samples=${ns:-0} ${err:+ERR=$err}"
}

{
  echo "RUN2 offload-stage contribution (25000 steps each; --privileged; A10G g5.2xlarge)"
  echo "preflight: $PRE  gpus_in_container: $(cat gpus.txt 2>/dev/null)"
  echo "gpu: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  if [ "$PRE" = OK ]; then
    run_cfg allgpu -nb gpu -pme gpu -update gpu
    run_cfg pmecpu -nb gpu -pme cpu -update gpu
    run_cfg updcpu -nb gpu -pme gpu -update cpu
    run_cfg allcpu -nb cpu
  fi
  echo "--- allgpu assignment (md.log) ---"; grep -iE 'selected for this run|PP:|will do|on the GPU|PME tasks|PP task' run_allgpu.log 2>/dev/null
  echo "--- util series per config (util%,mem_MB @1Hz) ---"
  for t in allgpu pmecpu updcpu allcpu; do echo "[$t]"; cat util_$t.csv 2>/dev/null; done
} > smoke-check.txt
echo "CANARY2 DONE preflight=$PRE"
