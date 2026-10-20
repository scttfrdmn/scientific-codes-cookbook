"""PySCF over a whole QM9 stratum: B3LYP/6-31G(2df,p) against QM9's published Gaussian 09 values, and
RHF -> MP2 (-> CCSD(T)) /cc-pVDZ for the Psi4 cross-check (psi4_run.py), one process per molecule.

    python3 pyscf_run.py <qm9 dir> <out dir> <workers> <max heavy atoms> <max heavy atoms for CCSD(T)> [probe]

Writes <out>/pyscf.jsonl (one record per molecule) and <out>/check.txt; exits 1 if any check fails."""
import json
import os
import resource
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qm9  # noqa: E402

QM9_DIR, OUT, NW, MAX_HA, CC_HA = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
PROBE = len(sys.argv) > 6 and sys.argv[6] == "probe"
MAXMEM = int(os.environ.get("PYSCF_MAX_MEMORY", "3000"))  # MB per worker process
GRID = (75, 302)            # Gaussian 09's default FineGrid: 75 radial shells x 302 angular points
INVARIANCE_EVERY = 25       # RHF is re-run rigidly rotated and translated for every 25th QM9 index

# Bounds. Each is argued in the README; none was picked to pass.
# B3LYP vs QM9 is two different (75,302) quadratures of the same functional in the same AO space: the
# probe measured each one's own error at up to ~3e-5 Ha. The bounds sit an order above that and an
# order below the smallest error of the wrong (library) 6-31G(2df,p) seen: 3.3e-4 Ha, NH3.
TOL_E = 1e-4      # Ha: B3LYP vs QM9 U0 - ZPVE (each printed to 1e-6)
TOL_ORB = 1e-4    # Ha: HOMO/LUMO vs QM9, printed to 1e-4 (rounding alone is 5e-5)
TOL_MU = 1e-3     # Debye: dipole vs QM9, printed to 1e-4; the wrong basis moves it 2e-2 - 6e-2
TOL_INV = 1e-8    # Ha: RHF under a rigid rotation + translation (exact integrals)
ATOMREF = {"H": (1, -0.500273), "C": (2, -37.846772), "N": (3, -54.583861), "O": (2, -75.064579), "F": (1, -99.718730)}


def b3lyp(atoms, spin=0, grid=GRID, prune=True, maxmem=MAXMEM, library=False):
    import numpy as np
    from pyscf import gto, dft
    basis = "6-31g(2df,p)" if library else qm9.gaussian_6_31g_2df_p([s for s, _ in atoms])
    mol = gto.M(atom=atoms, basis=basis, cart=True, spin=spin,
                verbose=0, max_memory=maxmem)
    mf = dft.UKS(mol) if spin else dft.RKS(mol)
    mf.xc = "b3lypg"            # Gaussian's B3LYP (VWN functional III, libxc HYB_GGA_XC_B3LYP)
    mf.grids.atom_grid = grid
    if not prune:
        mf.grids.prune = None
    mf.conv_tol = 1e-10
    qm9.restrict_to(mf, qm9.x_6d7f(mol))
    t = time.time()
    e = mf.kernel()
    r = dict(e=e, conv=bool(mf.converged), wall=time.time() - t, nao_cart=mol.nao, nbf=len(mf.mo_energy))
    if not spin:
        n = mol.nelectron // 2
        r.update(homo=float(mf.mo_energy[n - 1]), lumo=float(mf.mo_energy[n]),
                 mu=float(np.linalg.norm(mf.dip_moment(unit="Debye", verbose=0))))
    return r


def rhf(atoms, maxmem=MAXMEM):
    from pyscf import gto, scf
    mol = gto.M(atom=atoms, basis="cc-pvdz", verbose=0, max_memory=maxmem)
    mf = scf.RHF(mol)
    mf.conv_tol = 1e-11
    mf.conv_tol_grad = 1e-8
    mf.kernel()
    return mol, mf


def rotated(atoms, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    q, r = np.linalg.qr(rng.normal(size=(3, 3)))
    q = q * np.sign(np.diag(r))
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1
    t = rng.normal(scale=3.0, size=3)
    return [(s, tuple(float(v) for v in (np.array(x) @ q.T + t))) for s, x in atoms]


def job(d):
    from pyscf import mp, cc
    r = dict(idx=d["idx"], ha=d["ha"], natom=len(d["atoms"]), smiles=d["smiles"])
    t0 = time.time()
    try:
        b = b3lyp(d["atoms"])
        r.update({"b3lyp_" + k: v for k, v in b.items()})
        r.update(qm9_E=d["U0"] - d["zpve"], qm9_homo=d["homo"], qm9_lumo=d["lumo"], qm9_mu=d["mu"])
        t = time.time()
        mol, mf = rhf(d["atoms"])
        r.update(rhf=mf.e_tot, rhf_conv=bool(mf.converged), nbf_dz=mol.nao)
        nfz = d["ha"]   # one 1s core orbital per C, N, O, F -- Psi4's freeze_core
        m = mp.MP2(mf, frozen=nfz).run()
        r.update(mp2_corr=m.e_corr, rhf_mp2_wall=time.time() - t)
        if d["ha"] <= CC_HA:
            t = time.time()
            c = cc.CCSD(mf, frozen=nfz)
            c.conv_tol = 1e-10
            c.conv_tol_normt = 1e-7
            c.kernel()
            r.update(ccsd_corr=c.e_corr, ccsd_conv=bool(c.converged), t_corr=c.ccsd_t(), cc_wall=time.time() - t)
        if d["idx"] % INVARIANCE_EVERY == 0:
            _, mf2 = rhf(rotated(d["atoms"], d["idx"]))
            r.update(rhf_rotated=mf2.e_tot, rhf_rotated_conv=bool(mf2.converged))
            # control: PySCF's library 6-31G(2df,p), same AO space and grid, must miss QM9
            r["lib_e"] = b3lyp(d["atoms"], library=True)["e"]
        if PROBE and d["idx"] % 5 == 0:
            for tag, kw in [("big", dict(grid=(150, 974), prune=False)), ("incore", dict(maxmem=6000))]:
                bb = b3lyp(d["atoms"], **kw)
                r.update({"%s_%s" % (tag, k): v for k, v in bb.items() if k in ("e", "homo", "lumo", "mu", "wall", "conv")})
    except Exception as e:  # recorded, and counted as a failure by the checks
        r["error"] = repr(e)
    r["wall"] = time.time() - t0
    r["maxrss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return r


def atom_job(el):
    spin, ref = ATOMREF[el]
    b = b3lyp([(el, (0.0, 0.0, 0.0))], spin=spin)
    return dict(atom=el, e=b["e"], conv=b["conv"], qm9_U0=ref)


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    mols, seen, strata, nunc = qm9.load(QM9_DIR, MAX_HA)
    jobs = sorted(mols.values(), key=lambda d: (-len(d["atoms"]) - 4 * d["ha"], d["idx"]))
    if PROBE:
        keep = [d for d in jobs if d["ha"] <= 5]
        for ha in (6, 7):
            keep += [d for d in jobs if d["ha"] == ha][::max(1, len([d for d in jobs if d["ha"] == ha]) // 40)][:40]
        jobs = sorted(keep, key=lambda d: (-len(d["atoms"]) - 4 * d["ha"], d["idx"]))
    print("read %d QM9 records, %d uncharacterized; running %d molecules (<= %d heavy atoms), %d workers"
          % (seen, nunc, len(jobs), MAX_HA, NW), flush=True)
    res = []
    # ProcessPoolExecutor, not multiprocessing.Pool: a worker killed from outside (the OOM killer)
    # leaves a Pool waiting forever, while the executor raises BrokenProcessPool and the task fails.
    with ProcessPoolExecutor(NW) as ex, open(os.path.join(OUT, "pyscf.jsonl"), "w") as fh:
        atoms = list(ex.map(atom_job, sorted(ATOMREF)))
        futs = [ex.submit(job, d) for d in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            res.append(r)
            if i % 100 == 0 or i == len(jobs):
                print("%d/%d  %.0f s" % (i, len(jobs), time.time() - t0), flush=True)
    wall = time.time() - t0
    with open(os.path.join(OUT, "atoms.json"), "w") as fh:
        json.dump(atoms, fh, indent=1)
    sys.exit(check(res, atoms, seen, strata, nunc, len(jobs), wall))


def check(res, atoms, seen, strata, nunc, njobs, wall):
    lines, fails = [], []

    def ok(label, good, detail):
        good = bool(good)
        lines.append("%-4s %-52s %s" % ("ok" if good else "FAIL", label, detail))
        if not good:
            fails.append(label)

    def mx(vals):
        vals = list(vals)
        return max(vals) if vals else float("nan")

    ok("read the whole release", seen == 133885 and nunc == 3054, "%d records, %d uncharacterized" % (seen, nunc))
    ok("one record per selected molecule", len(res) == njobs and len({r["idx"] for r in res}) == njobs,
       "%d records, %d distinct" % (len(res), len({r["idx"] for r in res})))
    err = [r for r in res if "error" in r]
    ok("no molecule raised", not err, "%d raised %s" % (len(err), [(r["idx"], r["error"][:80]) for r in err[:3]]))
    good = [r for r in res if "error" not in r]
    ok("every B3LYP SCF converged", all(r["b3lyp_conv"] for r in good), "%d of %d" % (sum(r["b3lyp_conv"] for r in good), len(good)))
    ok("every RHF SCF converged", all(r["rhf_conv"] for r in good), "%d of %d" % (sum(r["rhf_conv"] for r in good), len(good)))
    ccr = [r for r in good if "ccsd_corr" in r]
    ok("every CCSD converged", all(r["ccsd_conv"] for r in ccr), "%d of %d" % (sum(r["ccsd_conv"] for r in ccr), len(ccr)))
    for a in atoms:
        d = abs(a["e"] - a["qm9_U0"])
        ok("atomref %s: UB3LYP == QM9 atomref U0" % a["atom"], a["conv"] and d <= TOL_E,
           "%.6f vs %.6f, |d| = %.2e <= %.0e" % (a["e"], a["qm9_U0"], d, TOL_E))
    dE = [abs(r["b3lyp_e"] - r["qm9_E"]) for r in good]
    nbad = sum(1 for v in dE if not v <= TOL_E)
    ok("B3LYP energy == QM9 U0 - ZPVE, every molecule", nbad == 0,
       "max |d| %.2e Ha <= %.0e (%d over); median %.2e" % (mx(dE), TOL_E, nbad, sorted(dE)[len(dE) // 2] if dE else float("nan")))
    for k in ("homo", "lumo"):
        v = [abs(r["b3lyp_" + k] - r["qm9_" + k]) for r in good]
        nb = sum(1 for x in v if not x <= TOL_ORB)
        ok("B3LYP %s == QM9, every molecule" % k.upper(), nb == 0, "max |d| %.2e Ha <= %.0e (%d over)" % (mx(v), TOL_ORB, nb))
    v = [abs(r["b3lyp_mu"] - r["qm9_mu"]) for r in good]
    nb = sum(1 for x in v if not x <= TOL_MU)
    ok("B3LYP dipole == QM9, every molecule", nb == 0, "max |d| %.2e D <= %.0e (%d over)" % (mx(v), TOL_MU, nb))
    inv = [abs(r["rhf_rotated"] - r["rhf"]) for r in good if "rhf_rotated" in r]
    ok("RHF invariant under rotation + translation", inv and mx(inv) <= TOL_INV and all(r["rhf_rotated_conv"] for r in good if "rhf_rotated" in r),
       "%d molecules, max |d| %.2e Ha <= %.0e" % (len(inv), mx(inv), TOL_INV))
    lib = [abs(r["lib_e"] - r["qm9_E"]) for r in good if "lib_e" in r]
    ok("control: library 6-31G(2df,p) misses QM9 on every molecule", lib and min(lib) > TOL_E,
       "%d molecules, min |d| %.2e Ha > %.0e (median %.2e)" % (len(lib), min(lib) if lib else float("nan"), TOL_E,
                                                             sorted(lib)[len(lib) // 2] if lib else float("nan")))
    ok("MP2 and CCSD(T) correlation energies negative", all(r["mp2_corr"] < 0 for r in good)
       and all(r["ccsd_corr"] < 0 and r["t_corr"] < 0 for r in ccr), "%d MP2, %d CCSD(T)" % (len(good), len(ccr)))
    lines.append("")
    lines.append("strata (heavy atoms: QM9 total, uncharacterized): " + ", ".join("%d: %d/%d" % (k, *strata[k]) for k in sorted(strata)))
    lines.append("molecules %d, CCSD(T) %d, rotated %d" % (len(good), len(ccr), len(inv)))
    lines.append("jobs_wall_s %.1f  cpu_s %.1f  max worker rss %.0f MiB" % (
        wall, sum(r["wall"] for r in res), mx(r["maxrss_mib"] for r in res)))
    if PROBE:
        big = [r for r in good if "big_e" in r]
        lines.append("probe: %d with big grid; max |E(75,302) - E(150,974)| %.2e, max |E(big) - QM9| %.2e" % (
            len(big), mx(abs(r["b3lyp_e"] - r["big_e"]) for r in big), mx(abs(r["big_e"] - r["qm9_E"]) for r in big)))
    lines.append("RESULT: %s" % ("PASS" if not fails else "FAIL " + ", ".join(fails)))
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(OUT, "check.txt"), "w") as fh:
        fh.write(txt + "\n")
    return 0 if (not fails or PROBE) else 1


if __name__ == "__main__":
    main()
