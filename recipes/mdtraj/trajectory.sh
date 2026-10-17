#!/usr/bin/env bash
# The mdtraj task, staged by stage-inputs.sh and pinned by sha256 in 01-trajectory.task.json
# (inline, the spec exceeds EC2's 16 KiB user-data limit).
set -eo pipefail
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
export PATH="${CONDA_PREFIX:-/opt/conda}/bin:$PATH"
export TMPDIR=/tmp HOME=/tmp LC_ALL=C GMX_MAXBACKUP=-1
cd /tmp
echo "== GROMACS writes a real XTC; MDTraj and MDAnalysis read the SAME bytes =="
# spc216 + tip3p ship in the GROMACS package, so a real trajectory needs zero staging:
# solvate a 6.2 nm box from the 216-water template, then run 100 ps.
GRO="${CONDA_PREFIX:-/opt/conda}/share/gromacs/top/spc216.gro"
printf '#include "amber99sb-ildn.ff/forcefield.itp"\n#include "amber99sb-ildn.ff/tip3p.itp"\n[ system ]\nwater\n[ molecules ]\n' > topol.top
gmx_mpi solvate -cs "$GRO" -box 6.2 6.2 6.2 -o big.gro -p topol.top > solvate.log 2>&1
NWAT=$(awk '/^SOL/{print $2; exit}' topol.top)
NATOM=$(awk -v n="$NWAT" 'BEGIN{print n*3}')
echo "solvated $NWAT waters = $NATOM atoms"

# 50,000 steps x 2 fs = 100 ps, a frame every 100 steps -> 501 frames.
# -ntomp is pinned: thread count changes the trajectory (MD is chaotic), which is why nothing
# below asserts a coordinate. The cross-library checks do not care -- both readers parse the
# SAME file, so their agreement is a property of the parsers, not of the dynamics.
# Energy minimisation FIRST: solvate packs waters that overlap, and dynamics started from
# those either blow up or collapse.
printf 'integrator = steep\nnsteps = 2000\nemtol = 100\ncutoff-scheme = Verlet\ncoulombtype = PME\nrvdw = 0.9\nrcoulomb = 0.9\n' > em.mdp
gmx_mpi grompp -f em.mdp -c big.gro -p topol.top -o em.tpr -maxwarn 5 > grompp_em.log 2>&1
gmx_mpi mdrun -s em.tpr -deffnm em -ntomp 8 -pin on > mdrun_em.log 2>&1
awk '/Potential Energy/{print "em_potential", $4}' mdrun_em.log || true

# PME rather than plain cut-off for water, and a thermostat so the run is a real NVT
# trajectory instead of drifting.
printf 'integrator = md\nnsteps = 50000\ndt = 0.002\ncutoff-scheme = Verlet\ncoulombtype = PME\nrvdw = 0.9\nrcoulomb = 0.9\nconstraints = h-bonds\ntcoupl = v-rescale\ntc-grps = System\ntau-t = 0.1\nref-t = 300\ngen-vel = yes\ngen-temp = 300\ngen-seed = 42\nnstxout-compressed = 100\n' > md.mdp
gmx_mpi grompp -f md.mdp -c em.gro -p topol.top -o t.tpr -maxwarn 5 > grompp.log 2>&1
T0=$(date +%s)
gmx_mpi mdrun -s t.tpr -deffnm out -ntomp 8 -pin on > mdrun.log 2>&1
tail -3 mdrun.log || true
T1=$(date +%s); WALL=$(( T1 - T0 ))
ls -l out.xtc | awk '{print "xtc_bytes", $5}'

python3 -u - <<'PYEOF' > smoke-check.txt || { cat smoke-check.txt; exit 1; }
import sys, numpy as np
import mdtraj as mdt
import MDAnalysis as mda
fail = []
def check(l, ok, detail):
    print(f"{l:26s} {detail}")
    if not ok: fail.append(l)

# getattr, not a guessed attribute path: `mdtraj.version` does not exist in this build and an
# AttributeError here threw away a completed 2.5-minute simulation. A diagnostic must never be
# able to fail the run it is describing.
print(f"mdtraj_version             {getattr(mdt, '__version__', 'unknown')}")
print(f"mdanalysis_version         {getattr(mda, '__version__', 'unknown')}")
t = mdt.load("out.xtc", top="big.gro")
u = mda.Universe("big.gro", "out.xtc")

# gmx solvate is deterministic for a fixed box and template, so the count is exact-or-wrong.
check("mdtraj_atoms", t.n_atoms == u.atoms.n_atoms == 23262,
      f"{t.n_atoms}  == MDAnalysis {u.atoms.n_atoms}  want 23262 (7754 waters x 3)")
check("frames", t.n_frames == len(u.trajectory) == 501,
      f"MDTraj {t.n_frames} / MDAnalysis {len(u.trajectory)}  want 501 (50000/100 + t=0)")

# THE parser identity: the same bytes decoded by two libraries must give the same numbers.
# Coordinates admit no model difference -- no masses, no definitions, nothing to disagree
# about -- so this is the check with nothing to argue over. (An earlier version compared
# mass-weighted Rg and failed at 4.9e-4 nm: the two libraries GUESS masses from atom names
# in a .gro and their tables differ slightly, which is a method difference masquerading as a
# parser bug. Rg is reported below, not asserted.)
rg_t = mdt.compute_rg(t)                                  # nm, used by the box check below
xyz_t = t.xyz                                             # nm
xyz_m = np.array([u.atoms.positions.copy() for _ in u.trajectory]) / 10.0   # A -> nm
d_xyz = float(np.max(np.abs(xyz_t - xyz_m)))
check("coords_two_libraries", d_xyz < 1e-5,
      f"max|MDTraj - MDAnalysis| = {d_xyz:.2e} nm over {xyz_t.size} values  want < 1e-5")

# Box volume per frame: also decode-only.
vol_t = np.array([np.linalg.det(b) for b in t.unitcell_vectors])               # nm^3
vol_m = np.array([u.trajectory.ts.volume for _ in u.trajectory]) / 1000.0      # A^3 -> nm^3
d_v = float(np.max(np.abs(vol_t - vol_m)))
check("volume_two_libraries", d_v < 1e-3,
      f"max|diff| = {d_v:.2e} nm^3  want < 1e-3 (same bytes, 2 parsers)")

# The physical check: constraints = h-bonds holds every O-H at tip3p's 0.09572 nm. An earlier
# run read a mean of 0.0246 nm, which is impossibly short -- so print the distribution and
# both periodic settings rather than guessing which assumption was wrong.
pairs = np.array([[i, i+1] for i in range(0, t.n_atoms, 3)])
oh_p = mdt.compute_distances(t, pairs, periodic=True)
oh_n = mdt.compute_distances(t, pairs, periodic=False)
print(f"oh_periodic_true           min {oh_p.min():.5f}  med {np.median(oh_p):.5f}  max {oh_p.max():.5f}")
print(f"oh_periodic_false          min {oh_n.min():.5f}  med {np.median(oh_n):.5f}  max {oh_n.max():.5f}")
oh = float(np.median(oh_n))
# The window is set by the FORCE FIELD, not by what this run produced: constraints = h-bonds
# holds every O-H at tip3p's 0.09572 nm exactly, and XTC stores coordinates to 0.001 nm, so a
# median outside +/-0.0005 of 0.09572 means the constraint was not applied -- not that the
# water moved. Observed median lands on 0.09572 to five decimals.
check("oh_constraint", abs(oh - 0.09572) < 5e-4,
      f"{oh:.5f} nm (median)  want 0.09572 +/-0.0005 (tip3p constraint, XTC rounds to 0.001)")

# Raw coordinates of the first water, so a bad trajectory shows itself rather than having to
# be deduced from a failing distance.
print("first_water_frame0         " + " ".join(f"{v:.4f}" for v in t.xyz[0][:3].ravel()))
print("first_water_frameN         " + " ".join(f"{v:.4f}" for v in t.xyz[-1][:3].ravel()))
# Rg of a filled box must be about L/2; this is the check that caught the collapsed run.
L = float(np.cbrt(vol_t.mean()))
check("rg_fills_box", abs(rg_t.mean() - L / 2) < 0.25,
      f"{rg_t.mean():.4f} nm vs L/2 = {L/2:.4f} nm  want within 0.25 (a filled box, not collapsed)")

rg_m = np.array([u.atoms.radius_of_gyration() for _ in u.trajectory]) / 10.0
print(f"rg_mdtraj_mean             {rg_t.mean():.4f} nm")
print(f"rg_mda_mean                {rg_m.mean():.4f} nm   (differs: the two guess masses)")
print(f"volume_mean                {vol_t.mean():.2f} nm^3")
if fail:
    print("FAILED: " + ", ".join(fail)); sys.exit(1)
PYEOF
echo "atoms              $NATOM" >> smoke-check.txt
echo "wall_s             $WALL" >> smoke-check.txt
cat smoke-check.txt
echo "SMOKE CHECK PASSED"
