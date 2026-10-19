"""vina-real: redock the PoseBusters paper's complexes with AutoDock Vina, and check what must hold.

    python3 -u dock.py <prepdir> <outdir> <workers> [<subset.txt>]

PoseBusters' Vina protocol (SI): a 25 A cube on the crystal ligand's centroid, exhaustiveness 32,
the top-ranked pose scored by RMSD to the crystal ligand. Each complex is one process with cpu=1
and seed=42, so its search is pinned and does not depend on how the pool schedules it.

Per complex:
  crystal pose  score_only, then local optimisation -- one function evaluation and one local
                minimisation: fixed points, the cross-chip comparison
  redock        from PoseBusters' own RDKit starting conformer -- a seeded Monte Carlo search: a
                path, reported per chip
  RMSD          symmetry-corrected, heavy atoms, no alignment (RDKit CalcRMS)
"""
import json
import multiprocessing
import os
import resource
import sys
import time

import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import rdMolAlign
from vina import Vina

RDLogger.DisableLog("rdApp.*")
PREP, OUT, W = sys.argv[1], sys.argv[2], int(sys.argv[3])
SUBSET = sys.argv[4] if len(sys.argv) > 4 else None
SEED, EXH, NPOSES = 42, 32, 9
REPEAT = 10          # cheapest complexes re-docked in fresh processes, to show the pinned search repeats
os.makedirs(os.path.join(OUT, "poses"), exist_ok=True)


def heavy_xyz(pdbqt):
    xyz = []
    for ln in pdbqt.splitlines():
        if ln.startswith(("ATOM", "HETATM")) and ln[77:79].strip() not in ("H", "HD", "HS"):
            xyz.append((float(ln[30:38]), float(ln[38:46]), float(ln[46:54])))
    return np.array(xyz)


def first_model(pdbqt):
    out = []
    for ln in pdbqt.splitlines():
        if ln.startswith("ENDMDL"):
            break
        if not ln.startswith("MODEL"):
            out.append(ln)
    return "\n".join(out) + "\n"


def models(pdbqt):
    cur, allm = [], []
    for ln in pdbqt.splitlines():
        if ln.startswith("MODEL"):
            cur = []
        elif ln.startswith("ENDMDL"):
            allm.append("\n".join(cur) + "\n")
        else:
            cur.append(ln)
    return allm


def pose_mol(sdf_mol_h, idx_map, xyz):
    """The SDF molecule (heavy atoms only) carrying the PDBQT pose's coordinates."""
    m = Chem.Mol(sdf_mol_h)
    conf = m.GetConformer()
    for k, i in enumerate(idx_map):
        conf.SetAtomPosition(i, xyz[k].tolist())
    return Chem.RemoveAllHs(m)


def generic(m):
    """Element-and-connectivity graph only: bond orders, aromaticity and charges dropped."""
    g = Chem.RWMol(m)
    for b in g.GetBonds():
        b.SetBondType(Chem.BondType.SINGLE)
        b.SetIsAromatic(False)
    for a in g.GetAtoms():
        a.SetIsAromatic(False)
        a.SetFormalCharge(0)
        a.SetNoImplicit(True)
    return g.GetMol()


def rmsd(probe, ref):
    """Symmetry-corrected heavy-atom RMSD in place. The start conformer and the crystal ligand
    are one molecule; where their bond-order/charge perception differs, match on the bare graph."""
    try:
        return float(rdMolAlign.CalcRMS(probe, ref))
    except RuntimeError:
        return float(rdMolAlign.CalcRMS(generic(probe), generic(ref)))


def dock_one(cid):
    t0 = time.time()
    d = os.path.join(PREP, cid)
    meta = json.load(open(os.path.join(d, "meta.json")))
    ref = Chem.MolFromMolFile(os.path.join(d, "ref.sdf"), removeHs=False)
    ref_h = Chem.RemoveAllHs(ref)
    start = Chem.MolFromMolFile(os.path.join(d, "start.sdf"), removeHs=False)
    r = {"complex": cid, "n_heavy": meta["n_heavy"], "n_torsions": meta["n_torsions"]}

    v = Vina(sf_name="vina", cpu=1, seed=SEED, verbosity=0)
    v.set_receptor(os.path.join(d, "rec.pdbqt"))
    # crystal pose: a function evaluation and a local minimisation
    v.set_ligand_from_file(os.path.join(d, "xtal.pdbqt"))
    v.compute_vina_maps(center=meta["centre"], box_size=meta["box"])
    xtal_txt = open(os.path.join(d, "xtal.pdbqt")).read()
    xyz = heavy_xyz(xtal_txt)
    r["rmsd_xtal_pdbqt"] = rmsd(pose_mol(ref, meta["xtal_map"], xyz), ref_h)
    r["e_xtal"] = float(v.score()[0])
    r["e_xtal_opt"] = float(v.optimize()[0])
    opt_path = os.path.join("/tmp", "opt_%s.pdbqt" % cid.replace("/", "_"))
    v.write_pose(opt_path, overwrite=True)
    r["rmsd_xtal_opt"] = rmsd(pose_mol(ref, meta["xtal_map"], heavy_xyz(open(opt_path).read())), ref_h)
    os.remove(opt_path)

    # redock from PoseBusters' starting conformer
    t1 = time.time()
    v = Vina(sf_name="vina", cpu=1, seed=SEED, verbosity=0)
    v.set_receptor(os.path.join(d, "rec.pdbqt"))
    v.set_ligand_from_file(os.path.join(d, "start.pdbqt"))
    v.compute_vina_maps(center=meta["centre"], box_size=meta["box"])
    r["rmsd_start_pdbqt"] = rmsd(pose_mol(start, meta["start_map"], heavy_xyz(open(os.path.join(d, "start.pdbqt")).read())),
                                 Chem.RemoveAllHs(start))
    v.dock(exhaustiveness=EXH, n_poses=NPOSES)
    e = v.energies(n_poses=NPOSES)
    poses = v.poses(n_poses=NPOSES)
    ms = models(poses)
    rms = [rmsd(pose_mol(start, meta["start_map"], heavy_xyz(m)), ref_h) for m in ms]
    r["dock_s"] = round(time.time() - t1, 2)
    r["n_poses"] = len(ms)
    r["e_top"] = float(e[0][0])
    r["energies_sorted"] = bool(all(e[i][0] <= e[i + 1][0] for i in range(len(e) - 1)))
    r["rmsd_top"] = rms[0]
    r["rmsd_best_of_poses"] = min(rms)
    # rescoring the written top pose with the same maps (coordinates rounded to 3 decimals)
    v.set_ligand_from_string(first_model(poses))
    r["e_top_rescored"] = float(v.score()[0])
    with open(os.path.join(OUT, "poses", cid.replace("/", "__") + ".pdbqt"), "w") as fh:
        fh.write(poses)
    r["wall_s"] = round(time.time() - t0, 2)
    r["pose_sha"] = __import__("hashlib").sha256(poses.encode()).hexdigest()[:16]
    return r


def safe(cid):
    try:
        return dock_one(cid)
    except Exception as ex:
        return {"complex": cid, "error": "%s: %s" % (type(ex).__name__, ex)}


manifest = [ln.split("\t") for ln in open(os.path.join(PREP, "manifest.tsv")).read().splitlines()[1:]]
cost = {c: (int(nt) + 1) * int(nh) for c, nh, nt in manifest}
ids = sorted(cost)
if SUBSET:
    want = set(open(SUBSET).read().split())
    ids = [c for c in ids if c in want]
# longest first, so the tail of the pool is short; the result does not depend on the order
order = sorted(ids, key=lambda c: -cost[c])
T0 = time.time()
res = {}
jl = open(os.path.join(OUT, "results.jsonl"), "w")   # as they finish, so a late failure keeps them
with multiprocessing.get_context("fork").Pool(W, maxtasksperchild=1) as pool:
    for k, r in enumerate(pool.imap_unordered(safe, order), 1):
        res[r["complex"]] = r
        jl.write(json.dumps(r) + "\n")
        jl.flush()
        print("%4d/%d %-32s %s" % (k, len(order), r["complex"],
              r.get("error") or "e_top %.3f rmsd_top %.2f dock_s %.0f" % (r["e_top"], r["rmsd_top"], r["dock_s"])), flush=True)
dock_wall = time.time() - T0
# the pinned search, again, in fresh processes: same seed + cpu -> the same poses, bit for bit
jl.close()
rep_ids = sorted(order[::-1][:REPEAT])
with multiprocessing.get_context("fork").Pool(W, maxtasksperchild=1) as pool:
    rep = {r["complex"]: r for r in pool.map(safe, rep_ids)}
peak_child_rss_gib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576

cols = ["complex", "n_heavy", "n_torsions", "e_xtal", "e_xtal_opt", "rmsd_xtal_opt", "e_top", "e_top_rescored",
        "rmsd_top", "rmsd_best_of_poses", "n_poses", "dock_s", "pose_sha"]
with open(os.path.join(OUT, "results.tsv"), "w") as fh:
    fh.write("\t".join(cols) + "\n")
    for c in ids:
        r = res[c]
        if "error" in r:
            continue
        fh.write("\t".join(repr(r[k]) if isinstance(r[k], float) else str(r[k]) for k in cols) + "\n")

fail = []
lines = []


def check(label, ok, detail):
    lines.append("%-34s %s %s" % (label, "ok  " if bool(ok) else "FAIL", detail))
    if not bool(ok):
        fail.append(label)


errs = [r for r in res.values() if "error" in r]
good = [res[c] for c in ids if "error" not in res[c]]
check("every_complex_docked", not errs and len(good) == len(ids), "%d of %d (%d errors%s)" % (
    len(good), len(ids), len(errs), "; first: %s %s" % (errs[0]["complex"], errs[0]["error"]) if errs else ""))
if not good:
    print("FAILED: no complex docked; first error: %s %s" % (errs[0]["complex"], errs[0]["error"]))
    sys.exit(1)
check("poses_returned", all(r["n_poses"] >= 1 for r in good), "min %d" % min(r["n_poses"] for r in good))
check("energies_ranked", all(r["energies_sorted"] for r in good), "every pose list ascending")
mx = max(r["rmsd_xtal_pdbqt"] for r in good)
check("crystal_pdbqt_rmsd_zero", mx < 2e-3, "max %.5f A  want < 0.002 (PDBQT keeps 3 decimals)" % mx)
mx = max(r["rmsd_start_pdbqt"] for r in good)
check("start_pdbqt_rmsd_zero", mx < 2e-3, "max %.5f A  want < 0.002" % mx)
bad = [c for c in rep_ids if "error" in rep[c] or any(rep[c][k] != res[c][k] for k in ("e_top", "rmsd_top", "pose_sha", "e_xtal", "e_xtal_opt"))]
check("pinned_search_repeats", not bad, "%d of %d re-docked complexes identical to the bit%s" % (
    len(rep_ids) - len(bad), len(rep_ids), "; differ: " + ",".join(bad) if bad else ""))
pb308 = set(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "posebusters_308_ids.txt")).read().split())
groups = {"astex": [r for r in good if r["complex"].startswith("astex/")],
          "posebusters_428": [r for r in good if r["complex"].startswith("posebusters/")],
          "posebusters_308": [r for r in good if r["complex"].startswith("posebusters/") and r["complex"].split("/")[1] in pb308]}
for g, rs in groups.items():
    if not rs:
        continue
    s = sum(r["rmsd_top"] <= 2.0 for r in rs)
    # A floor, not a band: a docking against wrong maps or a misplaced box lands near zero. The
    # published Vina rates on these sets are 52-60%, and this preparation is not PoseBusters' own.
    check("top1_success_%s" % g, s / len(rs) >= 0.25, "%d/%d = %.3f top-1 RMSD <= 2 A  want >= 0.25" % (s, len(rs), s / len(rs)))

m = {"n_complexes": len(ids), "n_docked": len(good), "dock_wall_s": round(dock_wall, 1),
     "peak_child_rss_gib": round(peak_child_rss_gib, 3), "workers": W, "seed": SEED, "exhaustiveness": EXH}
for g, rs in groups.items():
    if not rs:
        continue
    n = len(rs)
    m[g + "_n"] = n
    m[g + "_top1_le2A"] = sum(r["rmsd_top"] <= 2.0 for r in rs)
    m[g + "_any_pose_le2A"] = sum(r["rmsd_best_of_poses"] <= 2.0 for r in rs)
    m[g + "_xtal_opt_le2A"] = sum(r["rmsd_xtal_opt"] <= 2.0 for r in rs)
    # where the redock misses: Vina scores the wrong pose at or below the optimised crystal pose (the
    # scoring function prefers it), or above it (the search, from a rigid-ring starting conformer,
    # did not reach the crystal basin)
    miss = [r for r in rs if r["rmsd_top"] > 2.0]
    m[g + "_miss_scored_below_xtal"] = sum(r["e_top"] <= r["e_xtal_opt"] for r in miss)
    m[g + "_miss_scored_above_xtal"] = sum(r["e_top"] > r["e_xtal_opt"] for r in miss)
m["max_abs_rescore_minus_top"] = max(abs(r["e_top_rescored"] - r["e_top"]) for r in good)
m["n_xtal_opt_above_score"] = sum(r["e_xtal_opt"] > r["e_xtal"] for r in good)
m["sum_dock_s"] = round(sum(r["dock_s"] for r in good), 1)
with open(os.path.join(OUT, "metrics.txt"), "w") as fh:
    for k, val in m.items():
        fh.write("%s %s\n" % (k, val))
open(os.path.join(OUT, "checks.txt"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
print(open(os.path.join(OUT, "metrics.txt")).read())
if fail:
    print("FAILED: " + ", ".join(fail))
    sys.exit(1)
print("VINA REAL OK")
