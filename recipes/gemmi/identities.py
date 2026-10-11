#!/usr/bin/env python3
"""gemmi on Graviton4: crystallographic symmetry against the International Tables.

Most checks in this catalog have to construct their reference. Crystallography does not:
the number of general-position equivalents in a space group is a PUBLISHED INTEGER, so
gemmi's internal tables can be checked against an external authority with no tolerance
and no staged data at all.

The strongest check here is not a count though -- it is a group axiom. The symmetry
operations of a space group must be closed under composition, so every one of the 36,864
products of F m -3 m's 192 operations has to land back inside the set. A table with a
wrong or missing entry cannot satisfy that, and it needs no reference value whatsoever.

Two tolerances appear, and both are justified by a FORMAT or a DTYPE rather than by where
the run landed:

 * PDB stores coordinates in `%8.3f`, so its grid is 1e-3 A and the worst possible
   rounding error is half of that, 5.0e-4 A. That is a property of the format.
 * The three independent parsers all store float32 (biopython's get_coord() included --
   measured, after I assumed otherwise), so they cannot agree with gemmi's float64 more
   closely than one float32 ULP, which at |x| <= 50 A is 3.8e-06 A. Asserting bit-equality
   would fail on dtype, not on correctness.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import hashlib
import os
import sys
import warnings

import numpy as np
import gemmi

warnings.filterwarnings("ignore")

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("gemmi", getattr(gemmi, "__version__", "unknown"))
rec("numpy", np.__version__)
rec("python", "%d.%d.%d" % sys.version_info[:3])

# ------------------- 1. space-group orders against the International Tables, Volume A
# (Hermann-Mauguin name, space-group number, published order). These are tabulated
# values, not measurements -- an external authority this code had no part in choosing.
PUBLISHED = [("P 1", 1, 1), ("P -1", 2, 2), ("P 1 21 1", 4, 2), ("P 21 21 21", 19, 4),
             ("P 21/c", 14, 4), ("C 1 2/c 1", 15, 8), ("P 43 21 2", 96, 8),
             ("I 4", 79, 8), ("P 63/m m c", 194, 24), ("F d d d", 70, 32),
             ("I m -3 m", 229, 96), ("F m -3 m", 225, 192)]
rec("spacegroups_checked", "%d" % len(PUBLISHED))
for name, number, order in PUBLISHED:
    sg = gemmi.find_spacegroup_by_name(name)
    if sg is None:
        die("gemmi does not know the space group %r, which is number %d in the "
            "International Tables" % (name, number))
    n = len(list(sg.operations()))
    rec("sg_%s" % name.replace(" ", "_"), "number %d order %d" % (sg.number, n))
    if sg.number != number:
        die("%r is space group number %d in gemmi, %d in the International Tables"
            % (name, sg.number, number))
    if n != order:
        die("%r has %d operations in gemmi; the published order is %d"
            % (name, n, order))
rec("spacegroup_orders", "all %d match the published orders" % len(PUBLISHED))

# ------------------------ 2. the group axiom: closure under composition, exactly
# No reference value is needed. If a * b is not in the set for any pair, the operation
# list is not a group and the table is wrong.
for name in ("P 21 21 21", "P 63/m m c", "F m -3 m"):
    sg = gemmi.find_spacegroup_by_name(name)
    ops = list(sg.operations())
    triplets = {o.triplet() for o in ops}
    if len(triplets) != len(ops):
        die("%r lists %d operations but only %d distinct triplets -- duplicates"
            % (name, len(ops), len(triplets)))
    outside = 0
    for a in ops:
        for b in ops:
            # translations are modulo the lattice, so normalise before comparing
            if (a * b).wrap().triplet() not in triplets:
                outside += 1
    rec("closure_%s" % name.replace(" ", "_"),
        "%d ops, %d products, %d outside" % (len(ops), len(ops) ** 2, outside))
    if outside:
        die("%r is not closed under composition: %d of %d products fall outside the "
            "operation set, so it is not a group" % (name, outside, len(ops) ** 2))
rec("group_closure", "closed under composition in all three groups")

# ------------------------------------- 3. unit cell volume against the closed form
# V = abc sqrt(1 - cos^2 a - cos^2 b - cos^2 g + 2 cos a cos b cos g), for any lattice.
CELLS = [("orthorhombic", 40.96, 18.65, 22.52, 90.0, 90.0, 90.0),
         ("monoclinic", 10.0, 12.0, 14.0, 90.0, 102.5, 90.0),
         ("hexagonal", 7.5, 7.5, 9.1, 90.0, 90.0, 120.0)]
for label, a, b, c, al, be, ga in CELLS:
    cell = gemmi.UnitCell(a, b, c, al, be, ga)
    ca, cb, cg = (np.cos(np.radians(x)) for x in (al, be, ga))
    closed = a * b * c * np.sqrt(1 - ca**2 - cb**2 - cg**2 + 2*ca*cb*cg)
    if not np.isfinite(cell.volume) or not np.isfinite(closed):
        die("a non-finite cell volume for %s" % label)
    rec("volume_%s" % label, "gemmi %.6f closed %.6f diff %.3e"
        % (cell.volume, closed, abs(cell.volume - closed)))
    if cell.volume != closed:
        die("%s cell: gemmi gives %.17g, the closed form %.17g (diff %.3e)"
            % (label, cell.volume, closed, abs(cell.volume - closed)))
rec("cell_volume", "bit-identical to the closed form in all three lattice systems")

# ---------------------------------------------- 4. build a structure to round-trip
# 400 atoms over two chains. Synthetic, so no data is staged and the input coordinates
# are known exactly -- which is what lets the quantization below be stated as a bound.
SEED = 20261011
rng = np.random.default_rng(SEED)
st = gemmi.Structure()
st.spacegroup_hm = "P 21 21 21"
st.cell = gemmi.UnitCell(40.96, 18.65, 22.52, 90.0, 90.0, 90.0)
model = gemmi.Model("1")
orig = []
for cid in ("A", "B"):
    ch = gemmi.Chain(cid)
    for i in range(1, 51):
        res = gemmi.Residue()
        res.name = "ALA"
        res.seqid = gemmi.SeqId(i, " ")
        for an, el in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")):
            at = gemmi.Atom()
            at.name = an
            at.element = gemmi.Element(el)
            p = rng.uniform(-50.0, 50.0, 3)
            at.pos = gemmi.Position(*p)
            res.add_atom(at)
            orig.append(tuple(p))
        ch.add_residue(res)
    model.add_chain(ch)
st.add_model(model)
st.setup_entities()
orig = np.asarray(orig)
NATOM = len(orig)
rec("atoms", "%d" % NATOM)
rec("coord_range_A", "+/- 50")
if NATOM != 400:
    die("built %d atoms, expected 400" % NATOM)

st.write_pdb("structure.pdb")
st.make_mmcif_document().write_file("structure.cif")
rec("pdb_bytes", "%d" % os.path.getsize("structure.pdb"))
rec("cif_bytes", "%d" % os.path.getsize("structure.cif"))

# ------------- 5. the round-trip bounds, each set by the FORMAT not by the observation
# PDB's coordinate field is %8.3f: a 1e-3 A grid, so half a grid step is the worst
# achievable error. mmCIF writes more decimals and must therefore do better.
PDB_HALF_QUANTUM = 5.0e-4
for path, bound, why in (("structure.pdb", PDB_HALF_QUANTUM, "half of the %8.3f grid"),
                         ("structure.cif", 1.0e-6, "mmCIF carries more decimals")):
    back = gemmi.read_structure(path)
    got = np.array([[a.pos.x, a.pos.y, a.pos.z]
                    for m in back for ch in m for r in ch for a in r])
    if got.shape != orig.shape:
        die("%s read back %s atoms, wrote %s" % (path, got.shape, orig.shape))
    if not np.all(np.isfinite(got)):
        die("%s read back a non-finite coordinate" % path)
    worst = float(np.abs(got - orig).max())
    rec("roundtrip_%s" % path.split(".")[-1], "%.3e A (bound %.1e, %s)"
        % (worst, bound, why))
    if worst > bound:
        die("%s round-trip lost %.3e A, past the %.1e the format allows (%s)"
            % (path, worst, bound, why))
rec("format_quantization", "PDB loss is bounded by half its 1e-3 A coordinate grid")

# --------------- 6. three independent parsers on the SAME bytes gemmi just wrote
# gemmi's own re-read is the baseline: it is what the FILE says, in float64.
base = gemmi.read_structure("structure.pdb")
gc = np.array([[a.pos.x, a.pos.y, a.pos.z]
               for m in base for ch in m for r in ch for a in r])

# float32 cannot represent these values more finely than one ULP at their magnitude, so
# that -- not an arbitrary small number -- is the tolerance every referee is held to.
F32_ULP = float(np.spacing(np.float32(50.0)))
rec("float32_ulp_at_50A", "%.3e" % F32_ULP)

referees = {}
from Bio.PDB import PDBParser
s = PDBParser(QUIET=True).get_structure("x", "structure.pdb")
referees["biopython"] = np.array([a.get_coord() for a in s.get_atoms()], dtype="float64")

import MDAnalysis as mda
referees["mdanalysis"] = mda.Universe("structure.pdb").atoms.positions.astype("float64")

import mdtraj
referees["mdtraj"] = mdtraj.load("structure.pdb").xyz[0].astype("float64") * 10.0

import Bio
rec("biopython", Bio.__version__)
rec("mdanalysis", mda.__version__)
rec("mdtraj", getattr(mdtraj, "version", mdtraj).__dict__.get("full_version", "")
    or getattr(mdtraj, "__version__", "unknown"))

for name, arr in referees.items():
    if arr.shape != gc.shape:
        die("%s read %s atoms, gemmi read %s from the same file"
            % (name, arr.shape, gc.shape))
    worst = float(np.abs(arr - gc).max())
    rec("parser_%s_max_diff" % name, "%.3e A" % worst)
    # One ULP, with a little room for mdtraj's nm->A scaling, which costs a rounding.
    if worst > 2.0 * F32_ULP:
        die("%s disagrees with gemmi by %.3e A on the same file; float32 at this "
            "magnitude only explains %.3e" % (name, worst, F32_ULP))
rec("atom_count_agreement", "%d from gemmi and all %d referees"
    % (NATOM, len(referees)))
rec("cross_parser", "all referees within one float32 ULP of gemmi's float64")

# ---- the control: the comparison must be able to fail, or agreement proves nothing
perturbed = gc.copy()
perturbed[0, 0] += 0.01                      # 0.01 A: invisible to the eye, 2600x the ULP
ctl = float(np.abs(perturbed - gc).max())
rec("control_perturbation", "0.01 A shift gives max diff %.3e (%.0fx the ULP)"
    % (ctl, ctl / F32_ULP))
if not ctl > 2.0 * F32_ULP:
    die("a deliberate 0.01 A shift was not caught, so the parser comparison above "
        "cannot detect a real coordinate difference")

# ------------------------------------------------------------- 7. determinism
st.write_pdb("again.pdb")
h1 = hashlib.sha256(open("structure.pdb", "rb").read()).hexdigest()
h2 = hashlib.sha256(open("again.pdb", "rb").read()).hexdigest()
rec("pdb_sha256", h1)
rec("determinism", "byte-identical" if h1 == h2 else "DIFFERS")
if h1 != h2:
    die("writing the same structure twice gave different bytes")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("GEMMI OK")
