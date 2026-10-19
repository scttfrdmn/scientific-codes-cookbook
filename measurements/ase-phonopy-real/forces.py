"""GPAW (PBE, plane waves) forces on one of phonopy's committed displaced Si supercells.

    mpiexec -n <ranks> python3 -u forces.py <qe64|vasp16> <dir>

The displaced supercell is read from the committed phonopy_disp.yaml -- the exact structure the
reference code computed forces on -- and handed to GPAW through ASE. k-points match each
reference's own mesh in the supercell: QE's `4 4 4 1 1 1` (shifted Monkhorst-Pack) for qe64, and
the Gamma-centred 4x4x4 listed in vasprun.xml's kpointlist for vasp16. (A shifted mesh on the
16-atom FCC supercell keeps only 2 of its symmetries and puts ~2e-3 eV/A of spurious force on
atoms whose VASP force is ~1e-5.) Writes
forces-<case>.npy (eV/Angstrom, raw, drift NOT removed) and gpaw-<case>.json on rank 0.
"""
import json
import os
import resource
import sys
import time

import numpy as np
import phonopy
from ase import Atoms
from gpaw import GPAW, PW
from gpaw.mpi import world

CASE, D = sys.argv[1], sys.argv[2]
YAML = {"qe64": "Si-QE_phonopy_disp.yaml", "vasp16": "Si_phonopy_disp.yaml"}[CASE]
LUNIT = {"qe64": 0.52917721067, "vasp16": 1.0}[CASE]     # yaml length unit -> Angstrom
GAMMA = {"qe64": False, "vasp16": True}[CASE]             # each reference's own k-mesh

ph = phonopy.load(os.path.join(D, YAML), produce_fc=False, log_level=0)
sc = ph.supercells_with_displacements[0]
atoms = Atoms(symbols=sc.symbols, cell=np.array(sc.cell) * LUNIT,
              scaled_positions=sc.scaled_positions, pbc=True)
atoms.calc = GPAW(mode=PW(500), xc="PBE", kpts={"size": (4, 4, 4), "gamma": GAMMA},
                  occupations={"name": "fermi-dirac", "width": 0.01},
                  convergence={"density": 1e-6, "forces": 1e-5},
                  txt=os.path.join(D, "gpaw-%s.txt" % CASE))
t0 = time.time()
forces = atoms.get_forces()
energy = atoms.get_potential_energy()
wall = time.time() - t0
rss = np.array([resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20])   # KiB -> GiB, this rank
try:
    world.sum(rss)                      # every rank's peak, summed: the tool's resident footprint
except Exception:
    rss[:] = float("nan")
try:
    niter = int(atoms.calc.scf.niter)
except Exception:                       # describes the run only; must never fail it
    niter = "unknown"
if world.rank == 0:
    np.save(os.path.join(D, "forces-%s.npy" % CASE), forces)
    with open(os.path.join(D, "gpaw-%s.json" % CASE), "w") as fh:
        json.dump({"case": CASE, "natoms": len(atoms), "ranks": int(world.size), "energy": float(energy),
                   "wall": wall, "iterations": niter, "rss_gib_sum": float(rss[0]),
                   "gamma_centred_kmesh": GAMMA}, fh, indent=1)
    print("%s: %d atoms, %d ranks, E = %.8f eV, %.1f s, %s SCF iterations"
          % (CASE, len(atoms), world.size, energy, wall, niter), flush=True)
