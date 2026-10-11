#!/usr/bin/env python3
"""cctbx on Graviton4, cross-validated against gemmi across all 230 space groups.

This env was built to hold cctbx and gemmi together, and that is what the recipe uses:
two crystallography implementations sharing no code -- a Boost.Python C++ toolkit and a
modern C++ library -- asked the same questions about the same objects.

[recipes/gemmi] already checks gemmi's space-group orders against the International
Tables. This is the different and stronger claim: a SECOND implementation agrees with the
first, for every one of the 230 groups. Plus two exact invariances cctbx can be held to on
its own, three lattice-centring rules the space group determines, and a real structure that
ships INSIDE the installed package, so there is nothing to stage.

Both cross-checks carry a control, because an agreement proves nothing unless disagreement
was possible:
 * the 230-group comparison is re-run with gemmi's index SHIFTED by one, which must break
   it -- otherwise the comparison would be blind to group identity.
 * the centring rules are re-run on a PRIMITIVE lattice, where odd h+k+l must OCCUR --
   otherwise "no violations" might only mean the generator never emits them.

One unit trap, measured rather than assumed: cctbx keeps fractional coordinates and gemmi
Cartesian, so the structure comparison orthogonalises first. A raw diff of the two arrays
would be a units mismatch, not a disagreement.

Run with `python3 -u` so a killed run still has its output in command.log.
"""
import hashlib
import os
import sys

import numpy as np
import cctbx
import gemmi
from cctbx import sgtbx, uctbx, crystal, miller
import iotbx.pdb

ROWS = []


def rec(k, v):
    print("%s\t%s" % (k, v))
    ROWS.append((k, v))


def die(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


rec("cctbx", getattr(cctbx, "__version__", "unknown"))
rec("gemmi", gemmi.__version__)
rec("numpy", np.__version__)
try:
    import Bio
    rec("biopython", Bio.__version__)
except Exception as exc:
    rec("biopython", "unavailable (%s)" % type(exc).__name__)

# ------------- 1. the cross-check: every space group, two independent implementations
orders_cc, orders_g, mismatch = [], [], []
for i in range(1, 231):
    sym = sgtbx.space_group_symbols(i)
    n_cc = sgtbx.space_group(sym).order_z()
    sg_g = gemmi.find_spacegroup_by_number(i)
    if sg_g is None:
        die("gemmi does not know space group number %d" % i)
    n_g = len(list(sg_g.operations()))
    orders_cc.append(n_cc)
    orders_g.append(n_g)
    if n_cc != n_g:
        mismatch.append((i, sym.hermann_mauguin(), n_cc, n_g))
rec("spacegroups_compared", "%d" % len(orders_cc))
rec("distinct_order_values", ",".join(str(v) for v in sorted(set(orders_cc))))
rec("cctbx_vs_gemmi_mismatches", "%d" % len(mismatch))
if len(orders_cc) != 230:
    die("compared %d space groups, expected 230" % len(orders_cc))
if mismatch:
    die("cctbx and gemmi disagree on %d space groups, e.g. %s" % (len(mismatch), mismatch[:5]))
rec("cross_validation_spacegroups", "230 of 230 orders agree between cctbx and gemmi")

# the control: shift gemmi's index by one. A comparison that could not tell the groups
# apart would still score 230 here.
shifted = orders_g[1:] + orders_g[:1]
agree_shifted = sum(1 for a, b in zip(orders_cc, shifted) if a == b)
rec("control_shifted_agreements", "%d of 230" % agree_shifted)
if agree_shifted >= 230:
    die("shifting gemmi's space-group index by one still agreed 230/230, so the "
        "comparison above is blind to which group is which and proves nothing")
rec("control_shifted", "%d of 230 differ under a one-index shift" % (230 - agree_shifted))

# -------------------------------- 2. a real structure, shipped inside the package
# Not staged and not fetched: iotbx carries this for its own regression tests, so it is
# version-matched to cctbx by construction. The sha256 is asserted -- if a future cctbx
# changes the file, that is a changed input and wants a re-run, not a silent pass.
ROOT = os.path.dirname(os.path.dirname(cctbx.__file__))
PDB = os.path.join(ROOT, "iotbx", "regression", "data", "1yjp.pdb")
SHA = "93ad953fb3ff14e26229f0ec4f037e9d4aed17b5111fabd53ca510abdbb16c7c"
if not os.path.exists(PDB):
    die("iotbx no longer ships regression/data/1yjp.pdb; this recipe reads it from the "
        "installed package rather than staging a copy")
got = hashlib.sha256(open(PDB, "rb").read()).hexdigest()
rec("structure_path", PDB.replace(ROOT, "<site-packages>"))
rec("structure_bytes", "%d" % os.path.getsize(PDB))
rec("structure_sha256", got)
if got != SHA:
    die("1yjp.pdb in this image hashes %s, pinned %s -- the input changed, so every value "
        "below needs re-deriving rather than a hash edit" % (got, SHA))

xrs = iotbx.pdb.input(file_name=PDB).xray_structure_simple()
st = gemmi.read_structure(PDB)
st.setup_entities()

cell_cc = np.array(xrs.unit_cell().parameters())
cell_g = np.array([st.cell.a, st.cell.b, st.cell.c,
                   st.cell.alpha, st.cell.beta, st.cell.gamma])
rec("cell_cctbx", ",".join("%.3f" % v for v in cell_cc))
rec("cell_gemmi", ",".join("%.3f" % v for v in cell_g))
cell_diff = float(np.abs(cell_cc - cell_g).max())
rec("cell_max_abs_diff", "%.3e" % cell_diff)
if not np.all(np.isfinite(cell_cc)) or not np.all(np.isfinite(cell_g)):
    die("a non-finite cell parameter")
if cell_diff != 0.0:
    die("cctbx and gemmi read different cell parameters from the same file (max |diff| "
        "%.3e)" % cell_diff)

sg_cc = str(xrs.space_group_info()).replace(" ", "")
sg_g = (st.spacegroup_hm or "").replace(" ", "")
rec("spacegroup_cctbx", str(xrs.space_group_info()))
rec("spacegroup_gemmi", st.spacegroup_hm)
if sg_cc != sg_g:
    die("space group differs: cctbx %r vs gemmi %r" % (sg_cc, sg_g))

n_cc = xrs.scatterers().size()
n_g = sum(1 for m in st for ch in m for r in ch for a in r)
rec("atoms_cctbx", "%d" % n_cc)
rec("atoms_gemmi", "%d" % n_g)
if n_cc != n_g:
    die("atom counts differ: cctbx %d vs gemmi %d" % (n_cc, n_g))

# cctbx stores FRACTIONAL coordinates, gemmi CARTESIAN. Orthogonalise before comparing;
# a raw diff would be a units mismatch rather than a disagreement.
cart_cc = np.array([xrs.unit_cell().orthogonalize(s.site) for s in xrs.scatterers()])
cart_g = np.array([[a.pos.x, a.pos.y, a.pos.z]
                   for m in st for ch in m for r in ch for a in r])
if cart_cc.shape != cart_g.shape:
    die("coordinate arrays are %s and %s" % (cart_cc.shape, cart_g.shape))
if not (np.all(np.isfinite(cart_cc)) and np.all(np.isfinite(cart_g))):
    die("a non-finite coordinate")
coord_diff = float(np.abs(cart_cc - cart_g).max())
rec("coord_max_abs_diff_A", "%.3e" % coord_diff)
if coord_diff > 1e-12:
    die("coordinates differ by %.3e A after orthogonalisation; both read the same file, "
        "whose own precision is 1e-3 A" % coord_diff)
rec("cross_validation_structure",
    "same cell, space group, atom count and coordinates to %.1e A" % coord_diff)

# ------------------------------------------- 3. exact invariances cctbx alone must satisfy
CELLS = [("orthorhombic", (40.96, 18.65, 22.52, 90, 90, 90)),
         ("monoclinic", (10.0, 12.0, 14.0, 90, 102.5, 90)),
         ("hexagonal", (7.5, 7.5, 9.1, 90, 90, 120))]
for label, p in CELLS:
    uc = uctbx.unit_cell(p)
    rr = uc.reciprocal().reciprocal()
    d = max(abs(a - b) for a, b in zip(uc.parameters(), rr.parameters()))
    rec("reciprocal_roundtrip_%s" % label, "%.3e" % d)
    if d != 0.0:
        die("%s: taking the reciprocal twice changed the cell by %.3e" % (label, d))
    # and the volumes must match gemmi's, computed independently
    v_cc, v_g = uc.volume(), gemmi.UnitCell(*p).volume
    rec("volume_%s" % label, "cctbx %.9f gemmi %.9f diff %.3e"
        % (v_cc, v_g, abs(v_cc - v_g)))
    if v_cc != v_g:
        die("%s cell volume: cctbx %.17g vs gemmi %.17g" % (label, v_cc, v_g))
rec("reciprocal_invariance", "reciprocal of the reciprocal is the original cell, exactly")

A = 10.0
uc = uctbx.unit_cell((A, A, A, 90, 90, 90))
worst = 0.0
for hkl in ((1, 0, 0), (1, 1, 0), (1, 1, 1), (2, 0, 0), (3, 2, 1), (4, 4, 4)):
    h, k, l = hkl
    d_cc = uc.d(hkl)
    d_cf = A / float(h * h + k * k + l * l) ** 0.5
    worst = max(worst, abs(d_cc - d_cf) / d_cf)
rec("dspacing_worst_rel", "%.3e" % worst)
if worst > 1e-14:
    die("d-spacing departs from a/sqrt(h^2+k^2+l^2) by %.3e on a cubic cell" % worst)
rec("dspacing_closed_form", "matches a/sqrt(h^2+k^2+l^2) to %.1e" % worst)

# ------------------- 4. lattice centring forbids reflections; the space group decides
RULES = [("I m -3 m", "h+k+l even", (10, 10, 10, 90, 90, 90),
          lambda h, k, l: (h + k + l) % 2 == 0),
         ("F m -3 m", "h,k,l same parity", (10, 10, 10, 90, 90, 90),
          lambda h, k, l: h % 2 == k % 2 == l % 2),
         ("C 1 2/m 1", "h+k even", (10, 11, 12, 90, 90, 90),
          lambda h, k, l: (h + k) % 2 == 0)]
for sym, desc, cellp, rule in RULES:
    cs = crystal.symmetry(unit_cell=cellp, space_group_symbol=sym)
    idx = list(miller.build_set(crystal_symmetry=cs, anomalous_flag=False,
                                d_min=1.0).indices())
    bad = [t for t in idx if not rule(*t)]
    rec("absences_%s" % sym.replace(" ", "_"),
        "%s: %d reflections, %d violations" % (desc, len(idx), len(bad)))
    if not idx:
        die("%s generated no reflections, so its absence rule is untested" % sym)
    if bad:
        die("%s requires %s but %d of %d generated reflections break it, e.g. %s"
            % (sym, desc, len(bad), len(idx), bad[:3]))

# the control: on a PRIMITIVE lattice the forbidden combinations must OCCUR, or "no
# violations" above might only mean the generator never emits them at all.
cs_p = crystal.symmetry(unit_cell=(10, 10, 10, 90, 90, 90), space_group_symbol="P m -3 m")
idx_p = list(miller.build_set(crystal_symmetry=cs_p, anomalous_flag=False,
                              d_min=1.0).indices())
odd_p = [t for t in idx_p if sum(t) % 2 != 0]
rec("control_primitive_odd_sum", "%d of %d reflections have h+k+l odd"
    % (len(odd_p), len(idx_p)))
if not odd_p:
    die("a primitive lattice produced no reflection with h+k+l odd, so the I-centring "
        "check above is vacuous -- the generator simply never emits them")
rec("centring_rules", "I, F and C absences hold; P shows the combinations are reachable")

with open("score.tsv", "w") as fh:
    fh.write("observable\tvalue\n")
    for k, v in ROWS:
        fh.write("%s\t%s\n" % (k, v))

print("CCTBX OK")
