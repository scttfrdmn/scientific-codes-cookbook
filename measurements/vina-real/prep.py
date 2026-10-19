"""vina-real prep: turn the PoseBusters paper's complexes into Vina inputs, once, on one chip.

    python3 -u prep.py <posebusters_paper_data.zip> <outdir> <workers>

Per complex (Astex Diverse 85 + PoseBusters Benchmark 428):
  rec.pdbqt        the deposited protein, waters removed, hydrogens added at pH 7.4, rigid (Open Babel);
                   atoms of an element Vina has no type for (B, Mo, V, Xe) removed, and listed
  start.pdbqt      PoseBusters' own RDKit starting conformer (ligand_start_conf.sdf), what is docked
  xtal.pdbqt       the crystal ligand with hydrogens placed by RDKit, for the crystal-pose checks
  meta.json        box centre (crystal heavy-atom centroid; 25 A cube, PoseBusters' protocol, widened
                   only where the ligand cannot fit), receptor atoms Vina has no type for, and the
                   map from each PDBQT's heavy atoms to the crystal SDF's heavy atoms, by coordinate
  ref.sdf          the crystal ligand, the RMSD reference

Every input every chip docks is produced here, so the three chips dock the same bytes. A complex
that cannot be prepared is listed in prep_failed.tsv with the reason, never dropped silently.
"""
import json
import multiprocessing
import os
import subprocess
import sys
import zipfile

import numpy as np
from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")
ZIP, OUT = sys.argv[1], sys.argv[2]
SETS = {"astex_diverse_set": "astex", "posebusters_benchmark_set": "posebusters"}


def heavy_xyz_pdbqt(text):
    xyz, el = [], []
    for ln in text.splitlines():
        if ln.startswith(("ATOM", "HETATM")):
            t = ln[77:79].strip()
            if t in ("H", "HD", "HS"):
                continue
            xyz.append((float(ln[30:38]), float(ln[38:46]), float(ln[46:54])))
            el.append(t)
    return np.array(xyz), el


def coord_map(pdbqt_xyz, mol):
    """Index into mol's heavy atoms for each PDBQT heavy atom, matched by coordinate (< 0.01 A)."""
    ref = mol.GetConformer().GetPositions()
    heavy = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() > 1]
    out = []
    for p in pdbqt_xyz:
        d = np.linalg.norm(ref[heavy] - p, axis=1)
        j = int(np.argmin(d))
        if d[j] > 0.01:
            raise ValueError("pdbqt atom at %s has no SDF atom within 0.01 A (%.3f)" % (p, d[j]))
        out.append(heavy[j])
    if len(set(out)) != len(out) or len(out) != len(heavy):
        raise ValueError("pdbqt heavy atoms (%d) do not map 1:1 onto SDF heavy atoms (%d)" % (len(out), len(heavy)))
    return out


def drop_untyped(path):
    """Remove receptor atoms Vina has no type for (B, Mo, V, Xe... in ions and cofactors), one type at a
    time, by asking Vina to read the file; returns {type: count removed}."""
    from vina import Vina
    dropped = {}
    while True:
        try:
            Vina(sf_name="vina", cpu=1, verbosity=0).set_receptor(path)
            return dropped
        except Exception as e:
            msg = str(e)
            if "is not a valid AutoDock type" not in msg:
                raise
            t = msg.split("Atom type ", 1)[1].split(" ", 1)[0]
            lines = open(path).read().splitlines()
            keep = [ln for ln in lines if not (ln.startswith(("ATOM", "HETATM")) and ln[77:79].strip() == t)]
            if len(keep) == len(lines) or len(dropped) > 5:
                raise
            dropped[t] = len(lines) - len(keep)
            open(path, "w").write("\n".join(keep) + "\n")


def obabel(args):
    r = subprocess.run(["obabel"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("obabel %s: %s" % (" ".join(args), r.stderr.strip()[-300:]))


def prep(cid, files, d):
    os.makedirs(d, exist_ok=True)
    prot = files["protein.pdb"].decode()
    keep = [ln for ln in prot.splitlines() if not (ln.startswith(("ATOM", "HETATM")) and ln[17:20] in ("HOH", "WAT", "DOD"))]
    open(os.path.join(d, "protein_dry.pdb"), "w").write("\n".join(keep) + "\n")
    obabel(["-ipdb", os.path.join(d, "protein_dry.pdb"), "-xr", "-p", "7.4", "-opdbqt", "-O", os.path.join(d, "rec.pdbqt")])
    os.remove(os.path.join(d, "protein_dry.pdb"))
    dropped = drop_untyped(os.path.join(d, "rec.pdbqt"))

    xtal = Chem.MolFromMolBlock(files["ligand.sdf"].decode(), removeHs=False)
    if xtal is None:
        raise ValueError("crystal ligand SDF does not parse")
    Chem.MolToMolFile(xtal, os.path.join(d, "ref.sdf"))
    xtalH = Chem.AddHs(xtal, addCoords=True)
    Chem.MolToMolFile(xtalH, os.path.join(d, "xtal_h.sdf"))
    obabel(["-isdf", os.path.join(d, "xtal_h.sdf"), "-opdbqt", "-O", os.path.join(d, "xtal.pdbqt")])
    os.remove(os.path.join(d, "xtal_h.sdf"))

    start = Chem.MolFromMolBlock(files["ligand_start_conf.sdf"].decode(), removeHs=False)
    if start is None:
        raise ValueError("start conformer SDF does not parse")
    open(os.path.join(d, "start.sdf"), "w").write(files["ligand_start_conf.sdf"].decode())
    obabel(["-isdf", os.path.join(d, "start.sdf"), "-opdbqt", "-O", os.path.join(d, "start.pdbqt")])

    # The docked pose comes back in start.pdbqt's atom order; map it onto the START conformer's
    # heavy atoms (by coordinate), and those onto the crystal's by graph (start and crystal are
    # the same molecule in different conformations).
    sx, _ = heavy_xyz_pdbqt(open(os.path.join(d, "start.pdbqt")).read())
    start_map = coord_map(sx, start)
    s_heavy = Chem.RemoveAllHs(start)
    x_heavy = Chem.RemoveAllHs(xtal)
    if s_heavy.GetNumAtoms() != x_heavy.GetNumAtoms():
        raise ValueError("start (%d) and crystal (%d) heavy-atom counts differ" % (s_heavy.GetNumAtoms(), x_heavy.GetNumAtoms()))
    xx, _ = heavy_xyz_pdbqt(open(os.path.join(d, "xtal.pdbqt")).read())
    xtal_map = coord_map(xx, xtal)
    centre = x_heavy.GetConformer().GetPositions().mean(axis=0)
    # PoseBusters' 25 A cube, widened only where the ligand cannot fit in it: the crystal pose must lie
    # inside the box about its own centroid, and the starting conformer must fit in some orientation.
    xall = xtalH.GetConformer().GetPositions()
    sall = start.GetConformer().GetPositions()
    need = max(2 * float(np.abs(xall - centre).max()), float(np.linalg.norm(sall[:, None] - sall[None], axis=2).max())) + 1.0
    side = max(25.0, float(np.ceil(need)))
    ntors = sum(1 for ln in open(os.path.join(d, "start.pdbqt")) if ln.startswith("BRANCH"))
    meta = {"id": cid, "n_heavy": int(x_heavy.GetNumAtoms()), "n_torsions": ntors,
            "centre": [round(float(c), 3) for c in centre], "box": [side, side, side],
            "receptor_atoms_dropped": dropped,
            # heavy-atom indices of the H-bearing SDF each PDBQT came from, in PDBQT atom order
            "start_map": start_map, "xtal_map": xtal_map}
    json.dump(meta, open(os.path.join(d, "meta.json"), "w"))
    return meta


z = zipfile.ZipFile(ZIP)
by = {}
for name in z.namelist():
    parts = name.split("/")
    if len(parts) == 3 and parts[0] in SETS and parts[2]:
        cid = parts[1]
        suffix = parts[2][len(cid) + 1:]
        by.setdefault((parts[0], cid), {})[suffix] = name


def one(item):
    (s, cid), f = item
    need = ("protein.pdb", "ligand.sdf", "ligand_start_conf.sdf")
    tag = "%s/%s" % (SETS[s], cid)
    try:
        if not all(k in f for k in need):
            raise ValueError("missing " + ",".join(k for k in need if k not in f))
        zz = zipfile.ZipFile(ZIP)
        return tag, prep(cid, {k: zz.read(f[k]) for k in need}, os.path.join(OUT, SETS[s], cid)), None
    except Exception as e:
        return tag, None, str(e).replace("\t", " ").replace("\n", " ")


ok, failed = [], []
with multiprocessing.get_context("fork").Pool(int(sys.argv[3]) if len(sys.argv) > 3 else 1) as pool:
    for tag, m, why in pool.imap(one, sorted(by.items())):
        if why is None:
            ok.append((tag, m))
        else:
            failed.append((tag, why))
            print("PREP FAILED", tag, why, flush=True)
with open(os.path.join(OUT, "manifest.tsv"), "w") as fh:
    fh.write("complex\tn_heavy\tn_torsions\n")
    for tag, m in ok:
        fh.write("%s\t%d\t%d\n" % (tag, m["n_heavy"], m["n_torsions"]))
with open(os.path.join(OUT, "prep_notes.tsv"), "w") as fh:
    fh.write("complex\tbox_side_A\treceptor_atoms_dropped\n")
    for tag, m in ok:
        if m["box"][0] != 25.0 or m["receptor_atoms_dropped"]:
            fh.write("%s\t%g\t%s\n" % (tag, m["box"][0], ",".join("%s:%d" % kv for kv in sorted(m["receptor_atoms_dropped"].items())) or "-"))
with open(os.path.join(OUT, "prep_failed.tsv"), "w") as fh:
    fh.write("complex\treason\n")
    for tag, why in failed:
        fh.write("%s\t%s\n" % (tag, why))
print("complexes", len(by), "prepared", len(ok), "failed", len(failed))
for s in SETS.values():
    print(s, "prepared", sum(1 for t, _ in ok if t.startswith(s + "/")), "of", sum(1 for (k, _) in by if SETS[k] == s))
print("VINA PREP OK")
