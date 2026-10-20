#!/usr/bin/env python3
"""plumed-real: PLUMED's driver over a whole RODA peptide library, cross-checked per trajectory.

    python3 -u real.py <cohort.tar> <outdir> <workers> [reference-colvars.tar.gz]

For every trajectory in the bundle: cpptraj converts the published Amber NetCDF to DCD (which
PLUMED's molfile reader takes) and computes the same CVs independently; `plumed driver` computes
backbone phi/psi/omega, an L-chirality torsion per residue, Rg, end-to-end distance and every
SHAKE-constrained X-H distance; numpy recomputes all of it from the NetCDF bytes directly. With a
reference archive (Graviton4's COLVARs), every COLVAR is also compared with the other chip's.
"""
import hashlib
import io
import json
import math
import multiprocessing as mp
import os
import resource
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

import numpy as np

TOL_CV = 1e-9          # rad / nm: identical float32 inputs, double arithmetic, 1e-10 print unit
SHAKE_TOL = 1e-5       # sander's SHAKE tol (the .out's own "tol = 0.00001")
F32 = 2.0 ** -24       # float32 half-ulp, relative
K_ACOS = 32            # roundings in cpptraj's normalised dot product, in eps (see process())


def ver(mod):
    return str(getattr(mod, "__version__", "unknown"))


def prmtop(text):
    """The handful of %FLAG sections needed, parsed by their %FORMAT widths."""
    sec, fmt, cur = {}, {}, None
    for line in text.splitlines():
        if line.startswith("%FLAG"):
            cur = line.split()[1]; sec[cur] = []
        elif line.startswith("%FORMAT"):
            f = line[line.index("(") + 1:line.index(")")]
            n = int(f.split("a" if "a" in f else ("I" if "I" in f else "E"))[0])
            w = int(f.split("a")[1]) if "a" in f else (int(f.split("I")[1]) if "I" in f else int(f.split("E")[1].split(".")[0]))
            fmt[cur] = w
        elif cur and not line.startswith("%"):
            w = fmt[cur]
            sec[cur].extend(c for c in (line[i:i + w] for i in range(0, len(line), w)) if c.strip())
    names = [s.strip() for s in sec["ATOM_NAME"]]
    res = [s.strip() for s in sec["RESIDUE_LABEL"]]
    ptr = [int(s) - 1 for s in sec["RESIDUE_POINTER"]]
    z = [int(s) for s in sec["ATOMIC_NUMBER"]]
    beq = [float(s) for s in sec["BOND_EQUIL_VALUE"]]
    bh = [int(s) for s in sec["BONDS_INC_HYDROGEN"]]
    bonds = [(bh[i] // 3, bh[i + 1] // 3, beq[bh[i + 2] - 1]) for i in range(0, len(bh), 3)]
    return names, res, ptr, z, bonds


def torsion(p):
    """IUPAC dihedral of (F,4,3) float64 points."""
    b0 = p[:, 0] - p[:, 1]; b1 = p[:, 2] - p[:, 1]; b2 = p[:, 3] - p[:, 2]
    b1 = b1 / np.linalg.norm(b1, axis=1)[:, None]
    v = b0 - (b0 * b1).sum(1)[:, None] * b1
    w = b2 - (b2 * b1).sum(1)[:, None] * b1
    return np.arctan2((np.cross(b1, v) * w).sum(1), (v * w).sum(1))


def wrapd(a):
    return np.abs((a + np.pi) % (2 * np.pi) - np.pi)


def process(job):
    name, blob, ref = job
    base = name[:-len(".tar.bz2")]
    d = tempfile.mkdtemp(prefix="pr-", dir="/tmp/work")
    r = {"name": name}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:bz2") as tf:
            tf.extractall(d, filter="data")
        top = open(os.path.join(d, "p.1.top")).read()
        names, res, ptr, z, bonds = prmtop(top)
        nat = len(names)
        ptr.append(nat)
        atom = []
        for i in range(len(res)):
            atom.append({names[k]: k for k in range(ptr[i], ptr[i + 1])})
        R = len(res)
        tors = []  # (label, kind, residue index, 4 atoms 0-based)
        for i in range(R):
            if i < R - 1:
                tors.append(("psi%d" % (i + 1), "psi", i, (atom[i]["N"], atom[i]["CA"], atom[i]["C"], atom[i + 1]["N"])))
                tors.append(("omg%d" % (i + 1), "omg", i, (atom[i]["CA"], atom[i]["C"], atom[i + 1]["N"], atom[i + 1]["CA"])))
            if i > 0:
                tors.append(("phi%d" % (i + 1), "phi", i, (atom[i - 1]["C"], atom[i]["N"], atom[i]["CA"], atom[i]["C"])))
            if "CB" in atom[i]:
                tors.append(("chi%d" % (i + 1), "chir", i, (atom[i]["N"], atom[i]["CA"], atom[i]["C"], atom[i]["CB"])))
        heavy = [k for k in range(nat) if z[k] != 1]
        e2e = (atom[0]["N"], atom[R - 1]["C"])
        pl = []
        for lab, _, _, a in tors:
            pl.append("%s: TORSION ATOMS=%s NOPBC" % (lab, ",".join(str(x + 1) for x in a)))
        pl.append("rg: GYRATION TYPE=RADIUS ATOMS=%s NOPBC" % ",".join(str(x + 1) for x in heavy))
        pl.append("e2e: DISTANCE ATOMS=%d,%d NOPBC" % (e2e[0] + 1, e2e[1] + 1))
        for k, (a, b, _) in enumerate(bonds):
            pl.append("b%d: DISTANCE ATOMS=%d,%d NOPBC" % (k, a + 1, b + 1))
        labels = [t[0] for t in tors] + ["rg", "e2e"] + ["b%d" % k for k in range(len(bonds))]
        pl.append("PRINT ARG=%s FILE=COLVAR FMT=%%.10f" % ",".join(labels))
        open(os.path.join(d, "plumed.dat"), "w").write("\n".join(pl) + "\n")
        traj = base + ".dmd.1.traj"
        cp = ["parm p.1.top", "trajin %s" % traj, "trajout x.dcd dcd"]
        for lab, _, _, a in tors:
            cp.append("dihedral %s %s out cpp.dat" % (lab, " ".join("@%d" % (x + 1) for x in a)))
        cp.append("radgyr rg @%s nomax out cpp.dat" % ",".join(str(x + 1) for x in heavy))
        cp.append("distance e2e @%d @%d noimage out cpp.dat" % (e2e[0] + 1, e2e[1] + 1))
        cp.append("precision cpp.dat 24 14")
        cp.append("run")
        open(os.path.join(d, "cpp.in"), "w").write("\n".join(cp) + "\n")
        c = subprocess.run(["cpptraj", "-i", "cpp.in"], cwd=d, capture_output=True, text=True)
        if c.returncode != 0:
            raise RuntimeError("cpptraj rc %d: %s" % (c.returncode, c.stdout[-400:] + c.stderr[-400:]))
        t0 = time.time(); u0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        # --no-mpi: this is a serial driver, and the MPI build's MPI_Init alone costs ~1.06 s of
        # wall per invocation (measured, startup.task.json) for an identical COLVAR.
        p = subprocess.run(["plumed", "--no-mpi", "driver", "--mf_dcd", "x.dcd", "--plumed", "plumed.dat",
                            "--timestep", "10"], cwd=d, capture_output=True, text=True)
        r["plumed_s"] = time.time() - t0
        u1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        r["plumed_cpu"] = (u1.ru_utime - u0.ru_utime) + (u1.ru_stime - u0.ru_stime)
        if p.returncode != 0:
            raise RuntimeError("plumed rc %d: %s" % (p.returncode, p.stdout[-400:] + p.stderr[-400:]))
        colvar = open(os.path.join(d, "COLVAR"), "rb").read()
        r["colvar"] = colvar
        r["sha256"] = hashlib.sha256(colvar).hexdigest()
        # ---- the published bytes, read directly
        import netCDF4
        from mdtraj.formats import DCDTrajectoryFile
        with netCDF4.Dataset(os.path.join(d, traj)) as nc:
            nc.set_auto_mask(False)
            X = np.array(nc.variables["coordinates"][:])          # (F, N, 3) float32, Angstrom
        with netCDF4.Dataset(os.path.join(d, base + ".prod.1.crd")) as nc:
            nc.set_auto_mask(False)
            rst = np.array(nc.variables["coordinates"][:], dtype=np.float64)
        with DCDTrajectoryFile(os.path.join(d, "x.dcd")) as f:
            D = f.read()[0]
        F = X.shape[0]
        out = open(os.path.join(d, base + ".dmd.1.out")).read()
        body = out.split("A V E R A G E S")[0]
        r["frames"] = F
        r["traj_sha256"] = hashlib.sha256(open(os.path.join(d, traj), "rb").read()).hexdigest()
        r["atoms"] = nat
        steps = [int(l.split()[2]) for l in body.splitlines() if l.startswith(" NSTEP =")]
        # sander prints step 0 and then every ntpr=5000 steps; ntwx=5000 writes a frame at each
        # of the latter, so the energy records past step 0 are exactly the frames.
        r["sander_records"] = len([x for x in steps if x > 0])
        r["sander_steps_match"] = steps == [5000 * k for k in range(F + 1)]
        r["ig"] = [l.split("=")[1].strip() for l in out.splitlines() if l.strip().startswith("ig ")][:1]
        r["sander_done"] = "Final Performance Info" in out and "Run   done at" in out
        r["shake_tol_ok"] = "tol     =   0.00001" in out
        r["dcd_lossless"] = bool(D.dtype == np.float32 and D.shape == X.shape and np.array_equal(D, X))
        r["restart_is_last_frame"] = bool(np.array_equal(rst.astype(np.float32), X[-1]))
        # ---- PLUMED's numbers
        rows = [l.split() for l in colvar.decode().splitlines() if l.strip() and not l.startswith("#")]
        C = np.array(rows, dtype=np.float64)
        r["colvar_rows"] = C.shape[0]
        r["colvar_cols_ok"] = C.shape[1] == 1 + len(labels)
        col = {lab: C[:, j + 1] for j, lab in enumerate(labels)}
        cpp = np.loadtxt(os.path.join(d, "cpp.dat"))
        ccol = {lab: cpp[:, j + 1] for j, lab in enumerate([t[0] for t in tors] + ["rg", "e2e"])}
        Y = X.astype(np.float64)
        dn = dc = 0.0
        # cpptraj's Torsion() is acos(cos phi) with the sign from a triple product
        # (TorsionRoutines.cpp), so its forward error is ~k*eps/|sin phi|, unbounded as phi -> 0 or
        # pi (a trans omega), where acos is flat; PLUMED and numpy use atan2, which is not. So the
        # cross-code bound is TOL_CV plus that conditioning term, capped at acos's worst case
        # sqrt(2*k*eps). Where |sin phi| >= 0.01 the extra term is < 1e-12 and the bound is TOL_CV.
        eps = np.finfo(np.float64).eps; K = float(K_ACOS)
        dc_excess = -1.0; dc_raw = 0.0; dc_theta = 0.0; dc_norm = 0.0
        for lab, _, _, a in tors:
            ref_np = torsion(Y[:, list(a)])
            dn = max(dn, float(wrapd(col[lab] - ref_np).max()))
            dev = wrapd(col[lab] - np.radians(ccol[lab]))
            sn = np.abs(np.sin(ref_np))
            allowed = TOL_CV + np.minimum(K * eps / np.maximum(sn, 1e-300), math.sqrt(2 * K * eps))
            dc_excess = max(dc_excess, float((dev - allowed).max()))
            well = sn >= 0.01
            if well.any():
                dc = max(dc, float(dev[well].max()))
            j = int(dev.argmax())
            if dev[j] > dc_raw:
                dc_raw = float(dev[j]); dc_theta = float(ref_np[j])
            dc_norm = max(dc_norm, float((np.clip(dev - 1e-10, 0, None) * sn).max()))
        r["cpp_excess"] = dc_excess; r["cpp_raw"] = dc_raw; r["cpp_raw_theta"] = dc_theta
        r["cpp_norm"] = dc_norm
        H = Y[:, heavy]
        rg_np = np.sqrt(((H - H.mean(1, keepdims=True)) ** 2).sum(2).mean(1)) / 10
        e2e_np = np.linalg.norm(Y[:, e2e[0]] - Y[:, e2e[1]], axis=1) / 10
        dn = max(dn, float(np.abs(col["rg"] - rg_np).max()), float(np.abs(col["e2e"] - e2e_np).max()))
        dc = max(dc, float(np.abs(col["rg"] - ccol["rg"] / 10).max()), float(np.abs(col["e2e"] - ccol["e2e"] / 10).max()))
        r["max_dev_numpy"] = dn
        r["max_dev_cpptraj"] = dc
        # ---- SHAKE: every X-H bond is a constraint, so PLUMED must read the topology's r0
        xmax = float(np.abs(Y).max())
        sh_rel = 0.0; sh_np = 0.0; sh_bound = 0.0
        for k, (a, b, r0) in enumerate(bonds):
            rel = np.abs(col["b%d" % k] * 10 - r0) / r0
            sh_rel = max(sh_rel, float(rel.max()))
            sh_np = max(sh_np, float(np.abs(col["b%d" % k] - np.linalg.norm(Y[:, a] - Y[:, b], axis=1) / 10).max()))
            sh_bound = max(sh_bound, SHAKE_TOL * (1 + 1e-3) + 2 * math.sqrt(3) * F32 * xmax / r0 + 1e-9 / (r0 / 10))
        r["constraints"] = len(bonds)
        r["shake_max_rel"] = sh_rel
        r["shake_bound"] = sh_bound
        r["bond_dev_numpy"] = sh_np
        # ---- chirality: every L residue keeps its handedness on every frame
        chir = [col[lab] for lab, kind, _, _ in tors if kind == "chir"]
        allc = np.concatenate(chir) if chir else np.zeros(0)
        r["chiral_values"] = int(allc.size)
        r["chiral_pos"] = int((allc > 0).sum())
        r["chiral_min_abs"] = float(np.abs(allc).min()) if allc.size else 0.0
        # ---- observations: cis peptide bonds; Ramachandran region of the central residues
        om = np.concatenate([col[lab] for lab, kind, _, _ in tors if kind == "omg"])
        r["cis_frames"] = int((np.abs(om) < np.pi / 2).sum())
        r["cis_frames_xpro"] = int(sum((np.abs(col[lab]) < np.pi / 2).sum() for lab, kind, i, _ in tors
                                       if kind == "omg" and res[i + 1] == "PRO"))
        rama = {}
        for i in (1, 2, 3):
            ph = col["phi%d" % (i + 1)]; ps = col["psi%d" % (i + 1)]
            aR = int(((ph < 0) & (ps > -2.0944) & (ps < 0.8727)).sum())
            aL = int((ph > 0).sum())
            rama[res[i]] = [a + b for a, b in zip(rama.get(res[i], [0, 0, 0]), [aR, aL, F - aR - aL])]
        r["rama"] = rama
        r["residues"] = res
        # ---- cross-chip: the same PLUMED on the same bytes on the other chip
        if ref is not None:
            r["xchip_identical"] = ref == colvar
            if ref == colvar:
                r["xchip_max_dev"] = 0.0
            else:
                rr = np.array([l.split() for l in ref.decode().splitlines() if l.strip() and not l.startswith("#")], dtype=np.float64)
                if rr.shape != C.shape:
                    r["xchip_max_dev"] = float("inf")
                else:
                    dd = np.abs(rr - C)
                    for j, lab in enumerate(labels):
                        if j < len(tors):
                            dd[:, j + 1] = wrapd(rr[:, j + 1] - C[:, j + 1])
                    r["xchip_max_dev"] = float(dd.max())
        r["ok_local"] = True
    except Exception as e:
        r["error"] = "%s: %s" % (type(e).__name__, e)
        r["ok_local"] = False
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return r


def jobs(bundle, refpath):
    ref = tarfile.open(refpath, "r:gz") if refpath else None
    with tarfile.open(bundle, "r") as tf:
        for m in tf:
            blob = tf.extractfile(m).read()
            rb = None
            if ref is not None:
                rm = ref.next()
                while rm is not None and rm.name != m.name[:-len(".tar.bz2")] + ".colvar":
                    rm = ref.next()
                rb = ref.extractfile(rm).read() if rm is not None else b"<missing>"
            yield (m.name, blob, rb)


def batched(pool, gen, n=512):
    """imap over bounded batches: a bare imap would read the whole bundle into its task queue."""
    buf = []
    for j in gen:
        buf.append(j)
        if len(buf) == n:
            yield from pool.imap(process, buf, chunksize=4); buf = []
    if buf:
        yield from pool.imap(process, buf, chunksize=4)


def main():
    bundle, outdir, workers = sys.argv[1], sys.argv[2], int(sys.argv[3])
    refpath = sys.argv[4] if len(sys.argv) > 4 else None
    os.makedirs(outdir, exist_ok=True); os.makedirs("/tmp/work", exist_ok=True)
    import mdtraj, netCDF4
    print("numpy", ver(np), "mdtraj", ver(mdtraj), "netCDF4", ver(netCDF4))
    t0 = time.time()
    res = []
    agg_rama = {}
    seen = set()
    with mp.get_context("fork").Pool(workers) as pool, \
         tarfile.open(os.path.join(outdir, "colvars.tar.gz"), "w:gz", compresslevel=6) as ct, \
         open(os.path.join(outdir, "per_traj.tsv"), "w") as pt:
        pt.write("name\tframes\tatoms\tconstraints\tsha256\tplumed_s\tmax_dev_numpy\tmax_dev_cpptraj\tshake_max_rel\txchip_max_dev\ttraj_sha256\tig\tcpp_raw\tcpp_raw_theta\terror\n")
        for i, r in enumerate(batched(pool, jobs(bundle, refpath))):
            cv = r.pop("colvar", None)
            if cv is not None:
                ti = tarfile.TarInfo(r["name"][:-len(".tar.bz2")] + ".colvar")
                ti.size = len(cv); ti.mtime = 0
                ct.addfile(ti, io.BytesIO(cv))
            dup = r.get("traj_sha256") in seen
            seen.add(r.get("traj_sha256"))
            for k, v in ({} if dup else r.get("rama", {})).items():
                agg_rama[k] = [a + b for a, b in zip(agg_rama.get(k, [0, 0, 0]), v)]
            pt.write("%s\t%s\t%s\t%s\t%s\t%.3f\t%.3e\t%.3e\t%.3e\t%s\t%s\t%s\t%.3e\t%.6f\t%s\n" % (
                r["name"], r.get("frames"), r.get("atoms"), r.get("constraints"), r.get("sha256"),
                r.get("plumed_s", -1), r.get("max_dev_numpy", -1), r.get("max_dev_cpptraj", -1),
                r.get("shake_max_rel", -1), r.get("xchip_max_dev", "-"), r.get("traj_sha256"),
                ",".join(r.get("ig", [])), r.get("cpp_raw", -1), r.get("cpp_raw_theta", 0), r.get("error", "")))
            r.pop("rama", None); r.pop("residues", None)
            res.append(r)
            if (i + 1) % 1000 == 0:
                print("%d trajectories in %.0f s" % (i + 1, time.time() - t0), flush=True)
    wall = time.time() - t0
    # Determinism on one chip: the first 50 again, byte for byte.
    first = []
    with tarfile.open(bundle) as tf:
        for m in tf:
            first.append((m.name, tf.extractfile(m).read(), None))
            if len(first) == 50:
                break
    want = {r["name"]: r.get("sha256") for r in res}
    with mp.get_context("fork").Pool(workers) as pool:
        again = pool.map(process, first)
    rerun_same = sum(1 for r in again if r.get("sha256") and r["sha256"] == want[r["name"]])
    ru = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1048576.0
    s = {"trajectories": len(res), "wall_s": wall, "rerun_identical_of_50": rerun_same,
         "children_maxrss_gib": ru, "rama": agg_rama, "results": None}
    ok = [r for r in res if r.get("ok_local")]
    s["errors"] = [(r["name"], r.get("error")) for r in res if not r.get("ok_local")][:20]
    s["n_errors"] = len(res) - len(ok)
    def tot(k): return int(sum(r.get(k, 0) for r in ok))
    def mx(k): return float(max([r.get(k, 0.0) for r in ok] or [float("nan")]))
    s.update({
        "frames": tot("frames"), "sander_records": tot("sander_records"),
        "colvar_rows": tot("colvar_rows"), "constraints": tot("constraints"),
        "constraint_frames": int(sum(r["constraints"] * r["frames"] for r in ok)),
        "frames_200_each": all(r["frames"] == 200 for r in ok),
        "rows_eq_frames": all(r["colvar_rows"] == r["frames"] for r in ok),
        "records_eq_frames": all(r["sander_records"] == r["frames"] and r["sander_steps_match"] for r in ok),
        "sander_done": sum(1 for r in ok if r["sander_done"]),
        "shake_tol_ok": sum(1 for r in ok if r["shake_tol_ok"]),
        "cols_ok": sum(1 for r in ok if r["colvar_cols_ok"]),
        "dcd_lossless": sum(1 for r in ok if r["dcd_lossless"]),
        "restart_is_last_frame": sum(1 for r in ok if r["restart_is_last_frame"]),
        "max_dev_numpy": mx("max_dev_numpy"), "max_dev_cpptraj": mx("max_dev_cpptraj"),
        "bond_dev_numpy": mx("bond_dev_numpy"),
        "shake_max_rel": mx("shake_max_rel"), "shake_bound_min": float(min(r["shake_bound"] for r in ok)) if ok else float("nan"),
        "shake_within_bound": sum(1 for r in ok if r["shake_max_rel"] <= r["shake_bound"]),
        "chiral_values": tot("chiral_values"), "chiral_pos": tot("chiral_pos"),
        "chiral_min_abs": float(min(r["chiral_min_abs"] for r in ok)) if ok else float("nan"),
        "cis_frames": tot("cis_frames"), "cis_frames_xpro": tot("cis_frames_xpro"),
        "plumed_cpu_total_s": float(sum(r.get("plumed_cpu", 0) for r in ok)),
        "plumed_cpu_s": float(sum(r.get("plumed_s", 0) for r in ok)),
        "distinct_trajectories": len(set(r["traj_sha256"] for r in ok)),
        "seeds": sorted(set(",".join(r["ig"]) for r in ok)),
        "cpp_excess": mx("cpp_excess"), "cpp_raw": mx("cpp_raw"), "cpp_norm": mx("cpp_norm"),
        "cpp_raw_theta": float(max(ok, key=lambda r: r["cpp_raw"])["cpp_raw_theta"]) if ok else float("nan"),
    })
    if refpath:
        s["xchip_identical"] = sum(1 for r in ok if r.get("xchip_identical"))
        s["xchip_max_dev"] = mx("xchip_max_dev")
    json.dump(s, open(os.path.join(outdir, "summary.json"), "w"), indent=1, default=str)
    N = len(res)
    fail = []
    def check(label, good, detail):
        print("%-26s %s %s" % (label, "ok  " if bool(good) else "FAIL", detail))
        if not bool(good):
            fail.append(label)
    check("all_processed", s["n_errors"] == 0 and N > 0, "%d trajectories, %d errors %s" % (N, s["n_errors"], s["errors"][:3]))
    check("frames_conserved", s["frames_200_each"] and s["rows_eq_frames"] and s["records_eq_frames"],
          "%d frames = %d COLVAR rows = %d sander energy records" % (s["frames"], s["colvar_rows"], s["sander_records"]))
    check("sander_completed", s["sander_done"] == N and s["shake_tol_ok"] == N, "%d/%d .out reach 'Final Performance Info', SHAKE tol 1e-5" % (s["sander_done"], N))
    check("restart_is_last_frame", s["restart_is_last_frame"] == N, "%d/%d float32(restart) == last frame, bit for bit" % (s["restart_is_last_frame"], N))
    check("dcd_lossless", s["dcd_lossless"] == N, "%d/%d DCD PLUMED read == NetCDF bytes exactly" % (s["dcd_lossless"], N))
    check("colvar_columns", s["cols_ok"] == N, "%d/%d" % (s["cols_ok"], N))
    check("plumed_eq_numpy", s["max_dev_numpy"] <= TOL_CV, "max %.2e  want <= %.0e (rad, nm)" % (s["max_dev_numpy"], TOL_CV))
    check("plumed_eq_cpptraj", s["max_dev_cpptraj"] <= TOL_CV and s["cpp_excess"] <= 0,
          "max %.2e where |sin phi| >= 0.01 (want <= %.0e); everywhere within TOL + %d eps/|sin phi|: worst raw %.2e at phi = %.7f rad, max (dev-1e-10)*|sin phi| = %.1e"
          % (s["max_dev_cpptraj"], TOL_CV, K_ACOS, s["cpp_raw"], s["cpp_raw_theta"], s["cpp_norm"]))
    check("bonds_eq_numpy", s["bond_dev_numpy"] <= TOL_CV, "max %.2e nm over %d constraint-frames" % (s["bond_dev_numpy"], s["constraint_frames"]))
    check("shake_fixed_point", s["shake_within_bound"] == N,
          "max |r-r0|/r0 %.3e over %d constraint-frames; bound %.3e (tol + float32 storage)" % (s["shake_max_rel"], s["constraint_frames"], s["shake_bound_min"]))
    check("chirality_conserved", s["chiral_values"] > 0 and s["chiral_pos"] in (0, s["chiral_values"]),
          "%d chirality torsions, %d positive, min |value| %.3f rad" % (s["chiral_values"], s["chiral_pos"], s["chiral_min_abs"]))
    check("deterministic_rerun", rerun_same == 50, "%d/50 COLVARs byte-identical on a second pass" % rerun_same)
    if refpath:
        check("xchip_agree", s["xchip_max_dev"] <= TOL_CV, "max %.2e vs Graviton4; %d/%d COLVARs byte-identical" % (s["xchip_max_dev"], s["xchip_identical"], N))
    print("observed cis_frames          %d (|omega| < 90 deg, over %d omega-frames; %d of them X-Pro)" % (s["cis_frames"], s["frames"] * 4, s["cis_frames_xpro"]))
    print("observed distinct_trajectories %d of %d (sander seed ig = %s in every .out)" % (s["distinct_trajectories"], N, s["seeds"]))
    print("wall_s                       %.1f" % wall)
    print("plumed_driver_wall_s         %.1f (sum over trajectories)" % s["plumed_cpu_s"])
    print("plumed_driver_cpu_s          %.1f (user+sys, sum over trajectories)" % s["plumed_cpu_total_s"])
    print("children_maxrss_gib          %.3f" % ru)
    print("rama (central residues of the distinct trajectories: alphaR, alphaL, other frames)")
    for k in sorted(agg_rama):
        v = agg_rama[k]; t = sum(v)
        print("  %-4s %8d frames  aR %.3f  aL %.3f  other %.3f" % (k, t, v[0] / t, v[1] / t, v[2] / t))
    if fail:
        print("FAILED: " + ", ".join(fail)); sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
