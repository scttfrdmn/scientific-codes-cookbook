"""Psi4 on the same QM9 molecules as pyscf_run.py: RHF (exact PK integrals) -> MP2 (conventional)
-> CCSD(T) (conventional), cc-pVDZ, frozen core, then compared with PySCF's energies molecule by molecule.

    python3 psi4_run.py <qm9 dir> <out dir> <workers> <max heavy atoms> <max heavy atoms for CCSD(T)> <pyscf.jsonl> [probe]

Each molecule runs in its own Python process: a Psi4 that has raised once keeps stale state ("new DPD
instance before the old one was freed") and fails every molecule after it in the same process.
Writes <out>/psi4.jsonl and <out>/crosscheck.txt; exits 1 if any check fails."""
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.environ.get("PSI_SCRATCH", "/tmp/psi4scr")
TOL_RHF = 1e-8    # Ha; argued in the README
TOL_CORR = 1e-7   # Ha, each of MP2, CCSD and (T) correlation


def one(d, cc):
    """Child process: one molecule, result as one JSON line on stdout."""
    import psi4
    r = dict(idx=d["idx"], ha=d["ha"])
    psi4.core.be_quiet()
    psi4.set_num_threads(1)
    psi4.set_memory("2500 MB")
    psi4.core.IOManager.shared_object().set_default_path(os.environ["PSI_SCRATCH"])
    geom = "\n".join("%s %.10f %.10f %.10f" % (s, *x) for s, x in d["atoms"])
    psi4.geometry(geom + "\nunits angstrom\nsymmetry c1\nno_reorient\nno_com\n")
    psi4.set_options({"basis": "cc-pvdz", "scf_type": "pk", "e_convergence": 1e-11, "d_convergence": 1e-9,
                      "mp2_type": "conv", "cc_type": "conv", "freeze_core": True, "r_convergence": 1e-8})
    r["rhf"] = psi4.energy("scf")
    psi4.energy("mp2")
    r["mp2_corr"] = psi4.variable("MP2 CORRELATION ENERGY")
    if cc:
        psi4.energy("ccsd(t)")
        r["ccsd_corr"] = psi4.variable("CCSD CORRELATION ENERGY")
        r["t_corr"] = psi4.variable("(T) CORRECTION ENERGY")
    r["maxrss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print("RESULT " + json.dumps(r), flush=True)


def job(d, cc_ha):
    t0 = time.time()
    scr = os.path.join(SCRATCH, str(d["idx"]))
    os.makedirs(scr, exist_ok=True)
    env = dict(os.environ, PSI_SCRATCH=scr)
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), "--one", json.dumps(d), str(int(d["ha"] <= cc_ha))],
                       env=env, capture_output=True, text=True)
    du = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(scr) for f in fs)
    shutil.rmtree(scr, ignore_errors=True)   # this task's own scratch directory, never a staged input
    res = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
    if p.returncode == 0 and res:
        r = json.loads(res[-1][7:])
    else:
        r = dict(idx=d["idx"], ha=d["ha"], error="rc=%d %s" % (p.returncode, (p.stderr or p.stdout)[-400:]))
    r["wall"] = time.time() - t0
    r["scratch_left_mib"] = du / 2**20
    return r


def main(argv):
    sys.path.insert(0, HERE)
    import qm9
    QM9_DIR, OUT, NW, MAX_HA, CC_HA, PYSCF = argv[1], argv[2], int(argv[3]), int(argv[4]), int(argv[5]), argv[6]
    probe = len(argv) > 7 and argv[7] == "probe"
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(SCRATCH, exist_ok=True)
    py = {}
    for line in open(PYSCF):
        r = json.loads(line)
        if r["idx"] in py:
            raise SystemExit("duplicate idx %d in %s" % (r["idx"], PYSCF))
        py[r["idx"]] = r
    mols, seen, strata, nunc = qm9.load(QM9_DIR, MAX_HA)
    sel = set(py) if probe else set(mols)
    jobs = sorted((mols[i] for i in sel), key=lambda d: (-len(d["atoms"]) - 4 * d["ha"], d["idx"]))
    print("read %d QM9 records; %d molecules, %d from PySCF, %d workers" % (seen, len(jobs), len(py), NW), flush=True)
    res = []
    with ThreadPoolExecutor(NW) as ex, open(os.path.join(OUT, "psi4.jsonl"), "w") as fh:
        futs = [ex.submit(job, d, CC_HA) for d in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            res.append(r)
            if i % 100 == 0 or i == len(jobs):
                print("%d/%d  %.0f s" % (i, len(jobs), time.time() - t0), flush=True)
    return check(res, py, set(mols), time.time() - t0, probe, CC_HA, OUT)


def check(res, py, stratum, wall, probe, cc_ha, out):
    lines, fails = [], []

    def ok(label, good, detail):
        good = bool(good)
        lines.append("%-4s %-52s %s" % ("ok" if good else "FAIL", label, detail))
        if not good:
            fails.append(label)

    ps = {r["idx"]: r for r in res}
    ok("PySCF and Psi4 ran the same molecules", (probe or set(py) == set(ps) == stratum) and len(ps) == len(res),
       "PySCF %d, Psi4 %d, stratum %d" % (len(py), len(ps), len(stratum)))
    err = [r for r in res if "error" in r]
    ok("no Psi4 calculation failed", not err, "%d failed %s" % (len(err), [(r["idx"], r["error"][-160:]) for r in err[:3]]))
    both = [(py[i], ps[i]) for i in sorted(ps) if i in py and "error" not in ps[i] and "error" not in py[i]]
    for key, tol in (("rhf", TOL_RHF), ("mp2_corr", TOL_CORR), ("ccsd_corr", TOL_CORR), ("t_corr", TOL_CORR)):
        need = [a["idx"] for a, b in both if key in ("rhf", "mp2_corr") or a["ha"] <= cc_ha]
        d = [abs(a[key] - b[key]) for a, b in both if key in a and key in b]
        nbad = sum(1 for v in d if not v <= tol)
        ok("PySCF == Psi4: %s, every molecule" % key, d and len(d) == len(need) and nbad == 0,
           "%d of %d molecules, max |d| %.2e Ha <= %.0e (%d over), median %.2e"
           % (len(d), len(need), max(d) if d else float("nan"), tol, nbad, sorted(d)[len(d) // 2] if d else float("nan")))
    lines.append("")
    good = [r for r in res if "error" not in r]
    lines.append("jobs_wall_s %.1f  cpu_s %.1f  max molecule rss %.0f MiB  max scratch left %.0f MiB" % (
        wall, sum(r["wall"] for r in res), max([r["maxrss_mib"] for r in good] or [0]),
        max(r["scratch_left_mib"] for r in res)))
    lines.append("RESULT: %s" % ("PASS" if not fails else "FAIL " + ", ".join(fails)))
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(out, "crosscheck.txt"), "w") as fh:
        fh.write(txt + "\n")
    return 0 if not fails else 1


if __name__ == "__main__":
    if sys.argv[1] == "--one":
        one(json.loads(sys.argv[2]), sys.argv[3] == "1")
    else:
        sys.exit(main(sys.argv))
