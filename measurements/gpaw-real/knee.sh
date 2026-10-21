set -eo pipefail
export PATH=/opt/conda/bin:$PATH
export TMPDIR=/tmp HOME=/tmp
export OMP_NUM_THREADS=1
cd /tmp
cat > slab.py <<'PYEOF'
import os, sys, time
from ase.build import fcc111
from gpaw import GPAW, PW
from gpaw.mpi import world

# Pt(111), 3x3 surface cell, 4 layers, 7 A vacuum -- the canonical slab size for adsorption work.
# ASE builds it from its own lattice constants, so the structure is pinned by the image, not staged.
slab = fcc111("Pt", size=(3, 3, 4), vacuum=7.0)
slab.calc = GPAW(mode=PW(400), xc="PBE", kpts=(4, 4, 1),
                 occupations={"name": "fermi-dirac", "width": 0.05},
                 txt="gpaw.txt")
t0 = time.time()
e = slab.get_potential_energy()
wall = time.time() - t0
ef = slab.calc.get_fermi_level()
if world.rank == 0:
    with open("result.tsv", "w") as f:
        f.write("atoms\t%d\n" % len(slab))
        f.write("ranks\t%d\n" % world.size)
        f.write("energy_eV\t%.6f\n" % e)
        f.write("fermi_eV\t%.6f\n" % ef)
        f.write("wall_s\t%.1f\n" % wall)
PYEOF
echo '== same SCF at 4, 8 and 16 ranks on ONE box =='
# DFT total energy is deterministic, so it must be decomposition-invariant. That makes this a
# real identity rather than a band -- unlike MD, where chaos rules out an exact assertion.
: > knee.tsv
printf 'ranks\twall_s\tenergy_eV\titers\n' >> knee.tsv
for R in 4 8 16; do
  echo "--- $R ranks ---"
  mpiexec -n "$R" --allow-run-as-root --oversubscribe python3 slab.py
  E=$(awk -F'\t' '$1=="energy_eV"{print $2}' result.tsv)
  W=$(awk -F'\t' '$1=="wall_s"{print $2}' result.tsv)
  GOT=$(awk -F'\t' '$1=="ranks"{print $2}' result.tsv)
  IT=$(awk '/Converged after/{print $3; exit}' gpaw.txt)
  test "$GOT" = "$R"
  printf '%s\t%s\t%s\t%s\n' "$R" "$W" "$E" "$IT" >> knee.tsv
  mv gpaw.txt gpaw_$R.txt
done
cat knee.tsv
# every rank count must land on the same energy to 1e-4 eV -- far tighter than any physical
# tolerance, because the only difference between these runs is how the work was divided.
SPREAD=$(awk -F'\t' 'NR==2{mn=$3; mx=$3} NR>2{if($3<mn)mn=$3; if($3>mx)mx=$3} END{printf "%.6f", mx-mn}' knee.tsv)
BASE=$(awk -F'\t' 'NR==2{print $2}' knee.tsv)
{ cat knee.tsv
  echo ""
  echo "energy_spread_eV  $SPREAD  want < 0.0001 (decomposition invariance)"
  awk -F'\t' -v b="$BASE" 'NR>1{printf "speedup_%s_ranks  %.2fx  (vs 4 ranks)\n", $1, b/$2}' knee.tsv
} > knee.txt
cat knee.txt
awk -v s="$SPREAD" 'BEGIN{exit !(s<0.0001)}'
echo 'KNEE SWEEP DONE'