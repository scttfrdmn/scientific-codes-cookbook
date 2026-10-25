set -uo pipefail
export PATH=/usr/local/gromacs/avx2_256/bin:/usr/local/gromacs/bin:/opt/conda/bin:$PATH
# bypassing nventry (NGC) skips its env setup; source GMXRC so gmx finds libs+data
for g in /usr/local/gromacs/avx2_256/bin/GMXRC /usr/local/gromacs/bin/GMXRC /opt/conda/bin/GMXRC; do
  [ -f "$g" ] && . "$g" 2>/dev/null && break
done
cd /work
STATUS=OK; REASON=""; export OMP_NUM_THREADS=8
GMX=""
for c in gmx gmx_mpi; do command -v $c >/dev/null 2>&1 && { GMX=$c; break; }; done
if [ -z "$GMX" ]; then
  g=$(find /opt/conda /usr/local /usr -maxdepth 6 \( -name gmx -o -name gmx_mpi \) -type f 2>/dev/null | head -1)
  if [ -n "$g" ]; then export PATH="$(dirname "$g"):$PATH"; . "$(dirname "$g")/GMXRC" 2>/dev/null || true; GMX=$(basename "$g"); fi
fi
[ -z "$GMX" ] && { STATUS=FAIL; REASON="no-gmx-on-path"; }
# thread-MPI gmx takes -ntmpi 1; the MPI build (gmx_mpi) rejects it (1 rank implicit)
NTMPI="-ntmpi 1"; [ "$GMX" = gmx_mpi ] && NTMPI=""
if [ "$STATUS" = OK ]; then
  printf '#include "oplsaa.ff/forcefield.itp"\n#include "oplsaa.ff/spc.itp"\n[ system ]\nwater\n[ molecules ]\n' > topol.top
  $GMX solvate -cs spc216 -box 6 6 6 -o box.gro -p topol.top >solv.log 2>&1 || { STATUS=FAIL; REASON=solvate; }
fi
if [ "$STATUS" = OK ]; then
  printf 'integrator=md\nnsteps=25000\ndt=0.002\ncutoff-scheme=Verlet\ncoulombtype=PME\nrcoulomb=1.0\nrvdw=1.0\ntcoupl=V-rescale\ntc-grps=System\ntau-t=0.1\nref-t=300\nconstraints=h-bonds\n' > md.mdp
  $GMX grompp -f md.mdp -c box.gro -p topol.top -o bench.tpr -maxwarn 5 >grompp.log 2>&1 || { STATUS=FAIL; REASON=grompp; }
fi
NAT=$(grep -oiE "[0-9]+ atoms" grompp.log 2>/dev/null | head -1)
if [ "$STATUS" = OK ]; then
  timeout 400 $GMX mdrun -s bench.tpr -nsteps 25000 $NTMPI -ntomp 8 -nb cpu -g md.log >mdrun.log 2>&1 || { STATUS=FAIL; REASON=mdrun; }
fi
WALL=$(awk '/^ *Time:/{print $3}' md.log 2>/dev/null | tail -1)
NSDAY=$(awk '/Performance:/{print $2}' md.log 2>/dev/null | tail -1)
CPCT=$(awk '/^ *Time:/{print $4}' md.log 2>/dev/null | tail -1)
GMXVER=$($GMX --version 2>/dev/null | awk -F: '/GROMACS version/{print $2}' | tr -d ' ')
{ echo "status: $STATUS  reason: $REASON"
  echo "arch: $(uname -m)  gmx_version: ${GMXVER:-NA}"
  echo "system_atoms: ${NAT:-NA}"
  echo "cpu_run: -nb cpu $NTMPI -ntomp 8 (25000 steps)"
  echo "wall_s: ${WALL:-NA}  ns_per_day: ${NSDAY:-NA}  core_pct: ${CPCT:-NA}"
  echo "--- md.log perf tail ---"; awk '/Core t|Performance:|M E G A|Time:/{print}' md.log 2>/dev/null | tail -8
  echo "--- mdrun.log tail ---"; tail -12 mdrun.log 2>/dev/null
} > smoke-check.txt
echo "CPU CANARY DONE status=$STATUS ns/day=${NSDAY:-NA}"
