#!/usr/bin/env bash
# The ambertools task, staged by stage-inputs.sh and pinned by sha256 in 01-md.task.json
# (inline, the spec exceeds EC2's 16 KiB user-data limit).
set -eo pipefail
# The env's root: /opt/conda in the aarchsci image, $CONDA_PREFIX in the x86 twin.
P="${CONDA_PREFIX:-/opt/conda}"
export PATH=$P/bin:$PATH
export TMPDIR=/tmp HOME=/tmp LC_ALL=C GMX_MAXBACKUP=-1
cd /tmp

echo "== ff14SB and TIP3P ship in the ambertools package (zero staging) =="
test -f "$P"/dat/leap/cmd/leaprc.protein.ff14SB
test -f "$P"/dat/leap/cmd/leaprc.water.tip3p

echo "== tleap: capped alanine, in vacuo and solvated in a TIP3P octahedron =="
# solvateoct fills from a pre-equilibrated water box with no RNG, so the atom count is
# reproducible and can be asserted exactly rather than banded.
cat > l.in <<'EOF'
source leaprc.protein.ff14SB
source leaprc.water.tip3p
sys = sequence { ACE ALA NME }
saveamberparm sys vac.parm7 vac.rst7
solvateoct sys TIP3PBOX 10.0
saveamberparm sys sol.parm7 sol.rst7
quit
EOF
tleap -f l.in > tleap.log 2>&1 || { tail -40 tleap.log; exit 1; }

echo "== sander single point in vacuo, no cutoff (the cross-code reference state) =="
cat > sp.in <<'EOF'
single point
 &cntrl
  imin=1, maxcyc=1, ncyc=0, ntb=0, cut=9999.0, ntpr=1, igb=0,
 /
EOF
sander -O -i sp.in -p vac.parm7 -c vac.rst7 -o sp.out -r sp.rst > sander-sp.log 2>&1 \
  || { tail -40 sander-sp.log; tail -40 sp.out; exit 1; }

echo "== GROMACS single point on the SAME topology and coordinates =="
# ParmEd converts the prmtop; ff14SB has no CMAP and uniform 1-4 scaling (scee=1.2,
# scnb=2.0), so the conversion is lossless. Match the modes or the comparison is
# meaningless: a 10 nm box with a 4 nm cutoff puts every pair inside the cutoff and every
# periodic image outside it, which is what sander's cut=9999 in vacuo computes; and
# coulomb-modifier/vdw-modifier = none removes GROMACS' default potential shift, which
# sander does not apply.
# .gro writes 3 decimals in nm = 0.01 Angstrom. That rounding alone moved the bond energy
# by 6x (0.0206 -> 0.1406 kcal/mol) and made two correct codes look 0.29 kcal/mol apart --
# a file-format artifact masquerading as a force-field disagreement. precision=8 fixes it.
python3 -u - <<'PYEOF' > convert.log 2>&1 || { cat convert.log; exit 1; }
import parmed
from parmed.gromacs import GromacsGroFile
p = parmed.load_file("vac.parm7", xyz="vac.rst7")
p.box = [100., 100., 100., 90., 90., 90.]
p.save("topol.top", format="gromacs", overwrite=True)
GromacsGroFile.write(p, "conf.gro", precision=8)
print("parmed", parmed.__version__, "atoms", len(p.atoms))
PYEOF
cat convert.log
cat > gsp.mdp <<'EOF'
integrator       = md
nsteps           = 0
cutoff-scheme    = Verlet
pbc              = xyz
nstlist          = 10
verlet-buffer-tolerance = -1
rlist            = 4.0
coulombtype      = Cut-off
coulomb-modifier = none
rcoulomb         = 4.0
vdwtype          = Cut-off
vdw-modifier     = none
rvdw             = 4.0
DispCorr         = no
continuation     = yes
constraints      = none
EOF
gmx_mpi grompp -f gsp.mdp -c conf.gro -p topol.top -o gsp.tpr -maxwarn 20 > grompp.log 2>&1 \
  || { grep -A4 -iE '^ERROR|Fatal' grompp.log; exit 1; }
gmx_mpi mdrun -s gsp.tpr -deffnm gsp -ntomp 1 > gmx-mdrun.log 2>&1 \
  || { grep -A6 -iE '^ERROR|Fatal' gmx-mdrun.log; exit 1; }

echo "== solvated: minimise, heat to 300 K, then a 100 ps NVE production run =="
cat > min.in <<'EOF'
minimise
 &cntrl
  imin=1, maxcyc=2000, ncyc=1000, ntb=1, cut=9.0, ntpr=500,
 /
EOF
cat > heat.in <<'EOF'
heat to 300 K, Langevin
 &cntrl
  imin=0, nstlim=10000, dt=0.002, ntb=1, cut=9.0, ntc=2, ntf=2,
  ntt=3, gamma_ln=2.0, temp0=300.0, tempi=100.0, ig=20261003,
  ntpr=2000, ntwx=0, ntwr=10000,
 /
EOF
# NVE production. irest=1/ntx=5 is load-bearing: without them sander discards the
# equilibrated velocities and starts from rest, so the run is not NVE at 300 K at all --
# it heats up as potential converts to kinetic, and the "drift" measured is that artifact
# (1.15 kcal/mol over 2 ps, measured, versus the real number below).
cat > nve.in <<'EOF'
NVE production, 100 ps
 &cntrl
  imin=0, nstlim=100000, dt=0.001, ntb=1, cut=9.0, ntc=2, ntf=2,
  ntt=0, irest=1, ntx=5, ntpr=1000, ntwx=1000, ntwr=100000, ioutfm=1,
 /
EOF
T0=$(date +%s)
sander -O -i min.in -p sol.parm7 -c sol.rst7 -o min.out -r min.rst > sander-min.log 2>&1 \
  || { tail -30 sander-min.log; tail -30 min.out; exit 1; }
T1=$(date +%s)
sander -O -i heat.in -p sol.parm7 -c min.rst -o heat.out -r heat.rst > sander-heat.log 2>&1 \
  || { tail -30 sander-heat.log; tail -30 heat.out; exit 1; }
T2=$(date +%s)
sander -O -i nve.in -p sol.parm7 -c heat.rst -o nve.out -r nve.rst -x nve.nc > sander-nve.log 2>&1 \
  || { tail -30 sander-nve.log; tail -30 nve.out; exit 1; }
T3=$(date +%s)
echo "min $((T1-T0))s  heat $((T2-T1))s  nve $((T3-T2))s"

echo "== cpptraj: peptide heavy-atom RMS over the trajectory =="
# cpptraj gets the RMS; the rigid-water check below is done with mdtraj instead, because
# cpptraj's `distance` over MULTI-ATOM masks returns the distance between the two masks'
# centres of mass, not per-water bond lengths. `distance :WAT&@O :WAT&@H1` therefore
# measures the separation of two 614-atom centroids -- about 0.008 A -- which fails a
# 0.9572 A assertion for a reason that has nothing to do with SHAKE or the physics.
cat > cpp.in <<'EOF'
parm sol.parm7
trajin nve.nc
rms first :1-3&!@H= out rms.dat
run
quit
EOF
cpptraj -i cpp.in > cpptraj.log 2>&1 || { tail -30 cpptraj.log; exit 1; }

python3 -u - <<'PYEOF' > smoke-check.txt 2>&1 || { cat smoke-check.txt; exit 1; }
import re, sys, os
import numpy as np
KCAL = 4.184  # kJ/mol per kcal/mol, exact by definition
fail = []
def check(label, ok, detail):
    print(f"{label:28s} {detail}")
    if not ok: fail.append(label)
def obs(label, detail):
    print(f"{label:28s} {detail}   [observation]")

# --- what tleap built -------------------------------------------------------------
nat_vac = int(open("vac.rst7").read().splitlines()[1].split()[0])
nat_sol = int(open("sol.rst7").read().splitlines()[1].split()[0])
check("atoms_vacuum", nat_vac == 22, f"{nat_vac}  want exactly 22 (ACE-ALA-NME, ff14SB)")
# The count is a property of the AmberTools VERSION: solvateoct has no RNG (two runs give
# identical bytes), but 24.8 sized this octahedron to 693 waters and 26.0 sizes it to 614.
NWAT = 614
check("atoms_solvated", nat_sol == 22 + 3 * NWAT, f"{nat_sol}  want exactly {22 + 3 * NWAT} (+{NWAT} TIP3P)")
# 1864 = 22 peptide + 3*614 water, so the two counts pin the water count without a third read
check("water_count_consistent", (nat_sol - nat_vac) % 3 == 0 and (nat_sol - nat_vac)//3 == NWAT,
      f"{(nat_sol - nat_vac)//3} waters  want {NWAT} (and (sol-vac) divisible by 3)")

# --- cross-code: sander vs GROMACS on identical bytes -----------------------------
sp = open("sp.out").read()
# sander's minimisation energy block; take the first printed step
blk = re.search(r"NSTEP\s+ENERGY.*?\n\s*\S+\s+(-?\d+\.\d+E?[-+]?\d*)", sp, re.S)
terms = {}
for m in re.finditer(r"(BOND|ANGLE|DIHED|VDWAALS|EEL|1-4 VDW|1-4 EEL)\s*=\s*(-?\d+\.\d+)", sp):
    terms.setdefault(m.group(1), float(m.group(2)))
sander_pot = sum(terms[k] for k in ("BOND","ANGLE","DIHED","VDWAALS","EEL","1-4 VDW","1-4 EEL"))

glog = open("gsp.log").read()
# GROMACS writes the energy block as alternating label/value lines in 15-char fields. Read
# it BY NAME rather than by index -- the field list changes with the mdp (DispCorr adds
# one, constraints add Constr. rmsd), so a positional read is a bug waiting for a settings
# change.
gblk = glog[glog.rfind("Energies (kJ/mol)"):].splitlines()[1:]
gmx = {}
labels = None
for line in gblk:
    if not line.strip(): break
    fields = [line[i:i+15].strip() for i in range(0, len(line.rstrip()), 15)]
    if labels is None:
        labels = fields
    else:
        gmx.update({k: float(v) for k, v in zip(labels, fields) if v})
        labels = None
gmx_pot = gmx["Potential"] / KCAL if "Potential" in gmx else None
obs("sander_potential", f"{sander_pot:.4f} kcal/mol (7 terms summed)")
obs("gromacs_potential", f"{gmx_pot:.4f} kcal/mol" if gmx_pot is not None else "NOT PARSED")
# Two unrelated codebases, same ff14SB topology, same coordinates, matched cutoff
# treatment. The tolerance is set by PRINT precision, not by how close they happen to
# land: sander prints 4 decimals on each of 7 terms (+/-0.0004 kcal) and GROMACS prints
# 6 significant figures on a -335 kJ term (+/-0.0001 kcal). 0.01 kcal/mol is ~10x that
# floor and ~1e-4 of the largest term -- tight enough to catch a real force-field
# mismatch, loose enough that it cannot go flaky.
check("crosscode_sander_vs_gmx", gmx_pot is not None and abs(sander_pot - gmx_pot) < 0.01,
      f"|{sander_pot:.4f} - {gmx_pot:.4f}| = {abs(sander_pot-gmx_pot):.4f} kcal/mol"
      f"  want < 0.01 (print-precision floor ~0.0005)")
pair = [("BOND","Bond"),("ANGLE","Angle"),("DIHED","Proper Dih."),
        ("1-4 VDW","LJ-14"),("1-4 EEL","Coulomb-14"),
        ("VDWAALS","LJ (SR)"),("EEL","Coulomb (SR)")]
matched = [(a, b) for a, b in pair if a in terms and b in gmx]
check("crosscode_terms_parsed", len(matched) == 7,
      f"{len(matched)}/7 energy terms matched by name in both outputs")
if matched:
    worst_k, worst = max(((a, abs(terms[a] - gmx[b]/KCAL)) for a, b in matched),
                         key=lambda t: t[1])
    obs("crosscode_worst_term", f"{worst:.4f} kcal/mol ({worst_k}), over {len(matched)} terms")

# --- NVE: energy conservation over a real trajectory ------------------------------
nve = open("nve.out").read()
steps = nve.split("A V E R A G E S")[0]
etot = [float(m.group(1)) for m in re.finditer(r"Etot\s+=\s*(-?\d+\.\d+)", steps)]
temp = [float(m.group(1)) for m in re.finditer(r"TEMP\(K\)\s+=\s*(-?\d+\.\d+)", steps)]
check("nve_frames", len(etot) >= 100, f"{len(etot)} Etot prints  want >= 100 (100 ps at ntpr=1000)")
# The run really was NVE at temperature, not a cold start: irest=1 carried the velocities,
# so frame 0 already has kinetic energy. This is what makes the drift below meaningful.
check("nve_started_hot", temp and temp[0] > 200, f"T(frame 0) = {temp[0]:.2f} K  want > 200 (irest=1 kept velocities)")
drift = (etot[-1] - etot[0]) if etot else 9e9
span = (max(etot) - min(etot)) if etot else 9e9
obs("nve_etot_initial", f"{etot[0]:.4f} kcal/mol")
obs("nve_etot_final", f"{etot[-1]:.4f} kcal/mol")
obs("nve_drift_absolute", f"{drift:+.4f} kcal/mol over 100 ps ({drift/0.1:+.3f} per ns)")
obs("nve_etot_span", f"{span:.4f} kcal/mol (max - min)")
# Conservation of energy is a law, so the only question is how much the integrator's
# discretisation costs -- and the meaningful scale for that is thermal, not a fraction of
# the total. A drift of 0.1% of |Etot| sounds small but says nothing; what matters is how
# much energy leaks per degree of freedom relative to kT. The DoF count is computed below
# from the constraint count mdtraj reports, not assumed.
frac = abs(drift) / abs(etot[0]) if etot else 1.0
obs("nve_drift_fraction", f"|drift|/|Etot| = {frac:.2e}")
meanT = sum(temp)/len(temp) if temp else 0
check("nve_temperature", 240 < meanT < 360, f"{meanT:.1f} K mean  want 240-360 (equipartition after heating to 300)")

# --- SHAKE is an exact geometric constraint, read back with mdtraj ----------------
import mdtraj
traj = mdtraj.load("nve.nc", top="sol.parm7")
check("trajectory_decodes", traj.n_frames == 100 and traj.n_atoms == nat_sol,
      f"{traj.n_frames} frames x {traj.n_atoms} atoms  want 100 x {nat_sol} "
      f"(mdtraj reading sander's NetCDF + prmtop)")
top = traj.topology
def is_wat(a): return a.residue.name in ("WAT", "HOH")
oh_pairs, hh_pairs = [], []
for b in top.bonds:
    a1, a2 = b[0], b[1]
    if not (is_wat(a1) and is_wat(a2)): continue
    syms = {a1.element.symbol, a2.element.symbol}
    (oh_pairs if syms == {"O", "H"} else hh_pairs).append((a1.index, a2.index))
# AmberTools constrains THREE distances per rigid water, not two: the two O-H bonds and
# the H-H distance, which is how the HOH angle is held. mdtraj's prmtop topology shows
# all three, so a naive "all bonds in water" filter picks up 3 x 614 = 1842 pairs and the
# H-H ones sit at ~1.514 A -- which fails a 0.9572 A assertion for a reason unrelated to
# SHAKE. Split them by element and assert each for what it is.
check("water_constraint_count",
      len(oh_pairs) == 2 * NWAT and len(hh_pairs) == NWAT,
      f"{len(oh_pairs)} O-H + {len(hh_pairs)} H-H  want {2 * NWAT} + {NWAT} (3 per rigid water)")
d_oh = mdtraj.compute_distances(traj, np.array(oh_pairs), periodic=True) * 10.0  # nm -> A
d_hh = mdtraj.compute_distances(traj, np.array(hh_pairs), periodic=True) * 10.0
# TIP3P's O-H equilibrium length is 0.9572 A and ntc=2 holds it every step, so this is
# constraint satisfaction -- exact to SHAKE's tolerance, over every water in every frame.
worst_oh = float(np.abs(d_oh - 0.9572).max())
check("shake_holds_water", worst_oh < 1e-3,
      f"max|d(O-H) - 0.9572| = {worst_oh:.2e} A over {d_oh.size:,} measurements "
      f"({NWAT} waters x 2 x 100 frames)  want < 1e-3 (SHAKE, ntc=2)")
# The H-H distance's VALUE follows from the prmtop's equilibrium angle, so assert what
# SHAKE actually guarantees -- that it does not move -- and report the value.
hh_spread = float(d_hh.max() - d_hh.min())
check("water_stays_rigid", hh_spread < 1e-3,
      f"H-H spread {hh_spread:.2e} A over {d_hh.size:,} measurements  want < 1e-3")
obs("water_hh_distance", f"{float(d_hh.mean()):.5f} A (HOH angle "
    f"{2*np.degrees(np.arcsin(float(d_hh.mean())/2/0.9572)):.3f} deg)")

def col2(path):
    vals = []
    for line in open(path):
        if line.startswith("#"): continue
        parts = line.split()
        if len(parts) >= 2: vals.append(float(parts[1]))
    return vals
rms = col2("rms.dat")
obs("peptide_heavy_atom_rms", f"{max(rms):.3f} A max from frame 1" if rms else "NA")

# Now that the constraint count is known, state the conservation check on a physical
# scale. ntc=2 constrains every bond to hydrogen (3 per rigid water, plus the peptide's),
# and sander removes the 3 centre-of-mass translations.
n_constraints = len(oh_pairs) + len(hh_pairs) + sum(
    1 for b in top.bonds
    if not (is_wat(b[0]) and is_wat(b[1]))
    and "H" in (b[0].element.symbol, b[1].element.symbol))
ndof = 3 * traj.n_atoms - n_constraints - 3
kT = 0.0019872041 * 300.0  # kcal/mol at 300 K
leak = abs(drift) / ndof / 0.1  # kcal/mol per DoF per ns (the run is 100 ps)
obs("degrees_of_freedom", f"{ndof} (3x{traj.n_atoms} - {n_constraints} constraints - 3 COM)")
check("nve_conserved", leak < 0.05 * kT,
      f"{leak:.2e} kcal/mol per DoF per ns = {100*leak/kT:.2f}% of kT(300K)  "
      f"want < 5% (integrator leak far below thermal)")

if fail:
    print("FAILED: " + ", ".join(fail)); sys.exit(1)
PYEOF
echo "wall_min_s          $((T1-T0))" >> smoke-check.txt
echo "wall_heat_s         $((T2-T1))" >> smoke-check.txt
echo "wall_nve_100ps_s    $((T3-T2))" >> smoke-check.txt
awk -v s=$((T3-T2)) 'BEGIN{printf "ns_per_day           %.2f\n", 0.1/s*86400}' >> smoke-check.txt
# Versions as installed, read from the env rather than remembered; reporting only, never fatal.
{ ls "$P"/conda-meta | grep -E '^(ambertools|gromacs|parmed|mdtraj|numpy|libblas)-[0-9]' | sed 's/\.json$//; s/^/installed           /'; } >> smoke-check.txt || true
cat smoke-check.txt
echo "SMOKE CHECK PASSED"
