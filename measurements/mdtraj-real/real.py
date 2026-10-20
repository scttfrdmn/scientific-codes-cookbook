"""MDTraj over the whole Folding@home FAST run of SARS-CoV-2 Mpro (RODA), MDAnalysis on the same bytes.

    python3 -u real.py <fah dir> <out dir> <worker processes> [first N files]

Each worker takes one XTC (one FAST generation x kid) and does what an analysis of a trajectory
set does with MDTraj: load it against the topology, superpose and RMSD to the starting structure,
radius of gyration, backbone phi/psi, DSSP, Shrake-Rupley SASA, and a periodic CA-CA distance
matrix. MDAnalysis decodes the SAME file and computes the quantities it shares a definition for.
Everything is written to results.json (per file) for check.py; nothing here asserts, so a check
can never discard a finished analysis.

Then the openmm-mdanalysis-real cross-check on identical bytes: OpenMM's 100-frame JAC (DHFR) DCD,
read by MDTraj against the suite's prmtop, every constrained distance in every frame.
"""
import glob
import hashlib
import json
import os
import resource
import sys
import time
from multiprocessing import Pool

import numpy as np

FAH, OUT, NP = sys.argv[1], sys.argv[2], int(sys.argv[3])
NFILES = int(sys.argv[4]) if len(sys.argv) > 4 else 0
TOP = os.path.join(FAH, "input_files", "prot_masses.pdb")
TMP = "/var/tmp/rt"                   # the XTC round trip goes to the root disk, not the tmpfs
SEED = 20261019


def rec(*a):
    print(*[str(x) for x in a], flush=True)      # str() every value: a tuple must never unpack


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def maxrss_gib(who=resource.RUSAGE_SELF):
    return resource.getrusage(who).ru_maxrss / 1024.0 / 1024.0


def kabsch_msd(x, y):
    """Mean square deviation after optimal superposition, float64 SVD -- a third implementation."""
    x = x - x.mean(0); y = y - y.mean(0)
    u, s, vt = np.linalg.svd(x.T @ y)
    d = np.sign(np.linalg.det(u @ vt))
    s[-1] *= d
    return max(0.0, float(((x * x).sum() + (y * y).sum() - 2.0 * s.sum()) / len(x)))


def rand_rigid(rng):
    q = rng.normal(size=4); q /= np.linalg.norm(q)
    a, b, c, d = q
    R = np.array([[a*a+b*b-c*c-d*d, 2*(b*c-a*d), 2*(b*d+a*c)],
                  [2*(b*c+a*d), a*a-b*b+c*c-d*d, 2*(c*d-a*b)],
                  [2*(b*d-a*c), 2*(c*d+a*b), a*a-b*b-c*c+d*d]])
    return R, rng.uniform(-5, 5, size=3)


def one(path):
    import mdtraj as md
    import MDAnalysis as mda
    from MDAnalysis.analysis import rms
    from MDAnalysis.lib.distances import calc_dihedrals, self_distance_array
    name = os.path.basename(path)
    r = {"file": name, "bytes": os.path.getsize(path)}
    tm = {}
    t0 = time.time()
    ref = md.load(TOP)
    t = md.load(path, top=TOP)
    tm["mdtraj_load"] = time.time() - t0
    r.update(n_atoms=int(t.n_atoms), n_frames=int(t.n_frames), n_res=int(t.n_residues),
             top_atoms=int(ref.n_atoms), time_first=float(t.time[0]), time_last=float(t.time[-1]))
    dt = np.diff(t.time.astype(np.float64))
    r["dt_min"], r["dt_max"] = (float(dt.min()), float(dt.max())) if len(dt) else (0.0, 0.0)
    r["xyz_sha"] = sha(t.xyz)
    r["box_sha"] = sha(t.unitcell_lengths) if t.unitcell_lengths is not None else "none"
    r["xyz_absmax"] = float(np.abs(t.xyz).max())
    # Grid check: XTC stores integers at 1/precision nm; every decoded value must sit on the grid.
    q = t.xyz.astype(np.float64) * 1000.0
    r["grid_dev_max"] = float(np.abs(q - np.round(q)).max())

    # --- MDAnalysis decodes the same bytes --------------------------------------------------
    t0 = time.time()
    u = mda.Universe(TOP, path)
    xm = np.empty((len(u.trajectory), u.atoms.n_atoms, 3), np.float32)
    bm = np.empty((len(u.trajectory), 6), np.float64)
    tmda = np.empty(len(u.trajectory), np.float64)
    for k, ts in enumerate(u.trajectory):
        xm[k] = u.atoms.positions
        bm[k] = ts.dimensions
        tmda[k] = ts.time
    tm["mda_load"] = time.time() - t0
    r["mda_atoms"], r["mda_frames"] = int(u.atoms.n_atoms), int(len(u.trajectory))
    if xm.shape == t.xyz.shape:
        r["coord_diff_nm"] = float(np.abs(t.xyz.astype(np.float64) - xm.astype(np.float64) / 10.0).max())
        r["box_diff_nm"] = float(np.abs(t.unitcell_lengths.astype(np.float64) - bm[:, :3] / 10.0).max())
        r["angle_diff_deg"] = float(np.abs(t.unitcell_angles.astype(np.float64) - bm[:, 3:]).max())
        r["time_diff_ps"] = float(np.abs(t.time.astype(np.float64) - tmda).max())
    r["box_angles"] = [float(x) for x in t.unitcell_angles[0]]
    r["n_values"] = int(t.xyz.size)

    # --- XTC round trip: MDTraj writes, both libraries read back ----------------------------
    t0 = time.time()
    os.makedirs(TMP, exist_ok=True)
    rt = os.path.join(TMP, name)
    t.save_xtc(rt)
    t2 = md.load(rt, top=TOP)
    r["rt_frames"] = int(t2.n_frames)
    r["rt_diff_nm"] = float(np.abs(t2.xyz.astype(np.float64) - t.xyz.astype(np.float64)).max()) if t2.xyz.shape == t.xyz.shape else -1.0
    u2 = mda.Universe(TOP, rt)
    d = 0.0
    for k, ts in enumerate(u2.trajectory):
        d = max(d, float(np.abs(u2.atoms.positions.astype(np.float64) - xm[k].astype(np.float64)).max()))
    r["rt_mda_diff_A"] = d
    r["rt_bytes"] = os.path.getsize(rt)
    # Is MDTraj's writer GROMACS's encoder, byte for byte? (same integers in, same bitstream out)
    r["rt_file_identical"] = bool(open(rt, "rb").read() == open(path, "rb").read())
    os.remove(rt)                       # a file this task created, never a staged one
    del t2, u2
    tm["roundtrip"] = time.time() - t0

    # --- RMSD to the starting structure, on CA, three implementations -----------------------
    t0 = time.time()
    ca = t.topology.select("name CA")
    ca_m = u.select_atoms("name CA").indices
    r["n_ca"], r["ca_same"] = int(len(ca)), bool(np.array_equal(ca, ca_m))
    rm = md.rmsd(t, ref, 0, atom_indices=ca)                     # nm, float32, QCP
    tm["mdtraj_rmsd"] = time.time() - t0
    refca = ref.xyz[0][ca].astype(np.float64) * 10.0
    rmda = np.array([rms.rmsd(xm[k][ca].astype(np.float64), refca, center=True, superposition=True)
                     for k in range(len(xm))]) / 10.0            # Å -> nm, float64, QCP
    # on exactly MDAnalysis's float64 inputs, so the two differ only in the superposition algorithm
    rsvd = np.sqrt([kabsch_msd(xm[k][ca].astype(np.float64), refca) for k in range(len(xm))]) / 10.0
    r["rmsd_sha"] = sha(rm)
    r["rmsd_mean"], r["rmsd_max"] = float(rm.mean()), float(rm.max())
    r["msd_mdtraj_mda"] = float(np.abs(rm.astype(np.float64) ** 2 - rmda ** 2).max())     # nm^2
    r["msd_mda_svd"] = float(np.abs(rmda ** 2 - rsvd ** 2).max())
    # Invariance: a random rigid motion of the whole trajectory cannot change a superposed RMSD.
    rng = np.random.default_rng(SEED + int(hashlib.md5(name.encode()).hexdigest()[:6], 16))
    R, v = rand_rigid(rng)
    tr = t.slice(np.arange(t.n_frames), copy=True)
    tr.xyz = (t.xyz.astype(np.float64) @ R.T + v).astype(np.float32)
    rmr = md.rmsd(tr, ref, 0, atom_indices=ca)
    r["msd_rigid_motion"] = float(np.abs(rmr.astype(np.float64) ** 2 - rm.astype(np.float64) ** 2).max())
    rgr = md.compute_rg(tr)
    del tr

    # --- Rg, unweighted (so neither library's mass table enters) ----------------------------
    rg = md.compute_rg(t)
    rgm = np.array([np.sqrt(((x - x.mean(0)) ** 2).sum(1).mean()) for x in xm.astype(np.float64)]) / 10.0
    r["rg_sha"], r["rg_mean"] = sha(rg), float(rg.mean())
    r["rg_rel_mdtraj_mda"] = float(np.abs(rg - rgm).max() / rgm.min())
    r["rg_rel_rigid_motion"] = float(np.abs(rgr - rg).max() / rg.min())

    # --- backbone dihedrals: MDTraj vs MDAnalysis's own phi/psi selections ------------------
    t0 = time.time()
    worst, same = 0.0, True
    for kind in ("phi", "psi"):
        idx, ang = getattr(md, "compute_" + kind)(t)
        r[kind + "_sha"] = sha(ang)
        sel = [getattr(res, kind + "_selection")() for res in u.residues]
        quad = np.array([s.indices for s in sel if s is not None])
        same = same and quad.shape == idx.shape and bool(np.array_equal(quad, idx))
        if quad.shape == idx.shape:
            for k in range(len(xm)):
                x = xm[k].astype(np.float64)
                a = calc_dihedrals(x[quad[:, 0]], x[quad[:, 1]], x[quad[:, 2]], x[quad[:, 3]])
                dd = np.angle(np.exp(1j * (a - ang[k].astype(np.float64))))     # wrapped difference
                worst = max(worst, float(np.abs(dd).max()))
        r["n_" + kind] = int(idx.shape[0])
    r["dihedral_quads_same"], r["dihedral_diff_rad"] = bool(same), worst
    tm["dihedrals"] = time.time() - t0

    # --- periodic CA-CA distances: MDTraj min-image vs MDAnalysis min-image -----------------
    t0 = time.time()
    pairs = np.array([(i, j) for a, i in enumerate(ca) for j in ca[a + 1:]])
    dm = md.compute_distances(t, pairs, periodic=True)
    r["dist_sha"], r["n_pairs"] = sha(dm), int(len(pairs))
    worst = 0.0
    for k in range(len(xm)):
        dd = self_distance_array(xm[k][ca], box=bm[k]) / 10.0      # triclinic: lengths AND angles
        worst = max(worst, float(np.abs(dd - dm[k]).max()))
    r["dist_diff_nm"] = worst
    tm["distances"] = time.time() - t0

    # --- DSSP and SASA: MDTraj's, reported (MDAnalysis's DSSP is a different implementation) -
    t0 = time.time()
    ds = md.compute_dssp(t, simplified=True)
    tm["mdtraj_dssp"] = time.time() - t0
    r["dssp_sha"] = sha(ds.astype("U2"))
    r["dssp_H"], r["dssp_E"] = float((ds == "H").mean()), float((ds == "E").mean())
    try:
        from MDAnalysis.analysis.dssp import DSSP
        t0 = time.time()
        # MDAnalysis's DSSP wants one N, CA, C and O per residue; the C-terminus carries OC1/OC2.
        keep = [res.ix for res in u.residues if {"N", "CA", "C", "O"} <= set(res.atoms.names)]
        ag = u.residues[keep].atoms
        dm_ = DSSP(ag).run().results.dssp
        tm["mda_dssp"] = time.time() - t0
        mine = np.where(ds[:, keep] == "C", "-", ds[:, keep])
        r["dssp_mda_residues"] = len(keep)
        r["dssp_agree_mda"] = float((mine == dm_).mean()) if dm_.shape == mine.shape else -1.0
    except Exception as e:                # an observation, so it may not fail the analysis
        r["dssp_agree_mda"] = "error: %s" % str(e)[:200]
    t0 = time.time()
    sa = md.shrake_rupley(t, mode="residue")
    tm["mdtraj_sasa"] = time.time() - t0
    r["sasa_sha"], r["sasa_total_mean_nm2"] = sha(sa), float(sa.sum(1).mean())
    sa_atom = md.shrake_rupley(t[:5], mode="atom")
    r["sasa_atom_vs_residue_rel"] = float(np.abs(sa_atom.sum(1) - sa[:5].sum(1)).max() / sa[:5].sum(1).min())
    # per-frame series for the cross-chip comparison (compare.py); dihedrals and distances by digest
    np.savez_compressed(os.path.join(OUT, "arrays", name[:-4] + ".npz"), rmsd=rm, rg=rg,
                        sasa_total=sa.sum(1), dssp=np.searchsorted(np.array(["C", "E", "H", "NA"]), ds).astype(np.uint8))
    tm["total"] = sum(tm.values())
    r["timing_s"] = tm
    r["worker_maxrss_gib"] = maxrss_gib()
    return r


def jac_dcd(d):
    """openmm-mdanalysis-real's DCD on identical bytes: MDTraj and MDAnalysis, every constraint."""
    import mdtraj as md
    import MDAnalysis as mda
    out = {}
    prm, dcd = os.path.join(d, "JAC.prmtop"), os.path.join(d, "traj-JAC.dcd")
    c = np.load(os.path.join(d, "constraints-JAC.npz"))
    t = md.load(dcd, top=prm)
    u = mda.Universe(prm, dcd, format="DCD")
    out.update(n_atoms=int(t.n_atoms), n_frames=int(t.n_frames), mda_atoms=int(len(u.atoms)),
               mda_frames=int(len(u.trajectory)), n_constraints=int(len(c["d"])))
    dx, wt, wm, dd, n = 0.0, 0.0, 0.0, 0.0, 0
    dmt = md.compute_distances(t, np.stack([c["i"], c["j"]], 1), periodic=False)          # nm
    for k, ts in enumerate(u.trajectory):
        x = u.atoms.positions.astype(np.float64)
        dx = max(dx, float(np.abs(t.xyz[k].astype(np.float64) * 10.0 - x).max()))
        dev_m = np.abs(np.linalg.norm(x[c["i"]] - x[c["j"]], axis=1) - 10.0 * c["d"]) / (10.0 * c["d"])
        dev_t = np.abs(dmt[k].astype(np.float64) - c["d"]) / c["d"]
        wm, wt = max(wm, float(dev_m.max())), max(wt, float(dev_t.max()))
        dd = max(dd, float(np.abs(10.0 * dmt[k].astype(np.float64) - np.linalg.norm(x[c["i"]] - x[c["j"]], axis=1)).max()))
        n += dev_m.size
    out.update(coord_diff_A=dx, dist_diff_A=dd, worst_dev_mda=wm, worst_dev_mdtraj=wt, n_distances=n, xyz_sha=sha(t.xyz))
    return out


if __name__ == "__main__":
    import mdtraj as md
    import MDAnalysis as mda
    res = {"mdtraj_version": str(getattr(md, "__version__", "unknown")),
           "mdanalysis_version": str(getattr(mda, "__version__", "unknown")),
           "numpy_version": str(getattr(np, "__version__", "unknown")), "workers": NP}
    rec("mdtraj", res["mdtraj_version"], "mdanalysis", res["mdanalysis_version"], "numpy", res["numpy_version"])
    files = sorted(glob.glob(os.path.join(FAH, "FAST_simulations", "*.xtc")))
    if NFILES:
        files = files[:NFILES]
    res["n_files"] = len(files)
    os.makedirs(os.path.join(OUT, "arrays"), exist_ok=True)
    T0 = time.time()
    per = []
    with Pool(NP, maxtasksperchild=1) as pool:
        for r in pool.imap_unordered(one, files):
            per.append(r)
            rec(r["file"], "frames", r["n_frames"], "atoms", r["n_atoms"], "coord_diff", r.get("coord_diff_nm"),
                "rt", r["rt_diff_nm"], "total_s", "%.1f" % r["timing_s"]["total"])
    res["fah_wall_s"] = time.time() - T0
    res["files"] = sorted(per, key=lambda r: r["file"])
    T1 = time.time()
    jd = os.path.join(os.path.dirname(OUT.rstrip("/")), "jac")
    try:
        res["jac"] = jac_dcd(jd)
    except Exception as e:
        res["jac"] = {"error": str(e)[:300]}
    res["jac_wall_s"] = time.time() - T1
    res["main_maxrss_gib"] = maxrss_gib()
    res["children_maxrss_gib"] = maxrss_gib(resource.RUSAGE_CHILDREN)
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)
    rec("fah_wall_s", "%.1f" % res["fah_wall_s"], "jac", res["jac"], "children_maxrss_gib", "%.2f" % res["children_maxrss_gib"])
