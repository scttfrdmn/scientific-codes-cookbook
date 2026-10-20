"""Psi4 SAPT0 and CP-corrected DF-MP2 on the DESRES S66 dimers, as DESRES computed them, then
compared with the published DESS66 columns.

    python3 psi4_s66.py run <zip> <csv member> <out dir> <workers> <threads> <mem MB> <select> <conv>

<select> is `all`, or comma-separated CSV row numbers (0-based). <conv> is `tight` (SCF 1e-10) or
`loose` (SCF 1e-6, Psi4's SCF-module default, which is what sns-mp2's scf_helper calls ran at).

The calculation is DESRES's own harness, sns-mp2 (github.com/DEShawResearch/sns-mp2, master
@ 2020-12-01), read for its Psi4 calls: SAPT0 in the DESAVTZ basis (SAPT D_CONVERGENCE 1e-9, CPHF_R_CONVERGENCE since Psi4 1.10, which also removed SAPT E_CONVERGENCE) with the monomers in their own
(monomer-centred) basis, DF_BASIS_SAPT and DF_BASIS_ELST both the -RI basis; HF and DF-MP2 interaction
energies counterpoise-corrected; core frozen at one orbital per C, N, O. For H, C, N and O, which is
all of S66, DESAVTZ is aug-cc-pVTZ, its JK fit aug-cc-pVTZ-JKFIT and its RI fit aug-cc-pVTZ-RI.

Each geometry runs in its own Python process (a Psi4 that has raised keeps stale state). Writes
<out>/psi4.jsonl, <out>/check.txt and one Psi4 output per geometry under <out>/outputs/."""
import csv
import io
import json
import math
import os
import resource
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRATCH = os.environ.get("PSI_SCRATCH_ROOT", "/var/tmp/psi4scr")
SAPT_COLS = ["sapt_es", "sapt_ex", "sapt_exs2", "sapt_ind", "sapt_exind", "sapt_disp",
             "sapt_exdisp_os", "sapt_exdisp_ss", "sapt_delta_HF", "sapt_all"]
TZ_COLS = ["tz_HF", "tz_MP2_os", "tz_MP2_ss", "tz_MP2_all"]


def load(zpath, member):
    z = zipfile.ZipFile(zpath)
    rows = list(csv.DictReader(io.StringIO(z.read(member).decode())))
    out = []
    for i, r in enumerate(rows):
        el = r["elements"].split()
        xyz = [float(v) for v in r["xyz"].split()]
        n0, n1 = int(r["natoms0"]), int(r["natoms1"])
        if len(el) != n0 + n1 or len(xyz) != 3 * (n0 + n1):
            raise SystemExit("row %d: %d elements, %d coordinates, natoms %d+%d" % (i, len(el), len(xyz), n0, n1))
        out.append(dict(row=i, geom_id=int(r["geom_id"]), system_id=int(r["system_id"]), k_index=int(r["k_index"]),
                        name=r["system_name"], n0=n0, n1=n1, q0=int(r["charge0"]), q1=int(r["charge1"]),
                        atoms=[(el[a], xyz[3 * a:3 * a + 3]) for a in range(n0 + n1)],
                        ref={c: float(r[c]) for c in SAPT_COLS + TZ_COLS + ["reference", "cbs_CCSD(T)_all"]}))
    return out


def geometry(d, rot=None, shift=(0.0, 0.0, 0.0)):
    def xyz(p):
        if rot:
            p = [sum(rot[i][j] * p[j] for j in range(3)) for i in range(3)]
        return [p[i] + shift[i] for i in range(3)]
    a = ["%s %.10f %.10f %.10f" % (s, *xyz(p)) for s, p in d["atoms"][:d["n0"]]]
    b = ["%s %.10f %.10f %.10f" % (s, *xyz(p)) for s, p in d["atoms"][d["n0"]:]]
    return ("%d 1\n%s\n--\n%d 1\n%s\nunits angstrom\nsymmetry c1\nno_reorient\nno_com\n"
            % (d["q0"], "\n".join(a), d["q1"], "\n".join(b)))


def one(d, threads, mem, conv, outfile, rot):
    """Child process: one geometry; result as one JSON line on stdout."""
    import psi4
    t0 = time.time()
    psi4.set_output_file(outfile, False)
    psi4.set_memory("%d MB" % mem)
    psi4.set_num_threads(threads)
    psi4.core.IOManager.shared_object().set_default_path(os.environ["PSI_SCRATCH"])
    ctl = bool(rot and rot.get("control"))
    if rot and not ctl:
        mol = psi4.geometry(geometry(d, rot["R"], rot["t"]))
    else:
        mol = psi4.geometry(geometry(d))
    e = 1e-10 if conv == "tight" else 1e-6
    opts = {"basis": "aug-cc-pvtz", "df_basis_scf": "aug-cc-pvtz-jkfit", "df_basis_mp2": "aug-cc-pvtz-ri",
            "df_basis_sapt": "aug-cc-pvtz-ri", "df_basis_elst": "aug-cc-pvtz-ri",
            "scf_type": "df", "mp2_type": "df", "freeze_core": True,
            "e_convergence": e, "d_convergence": e,
            "sapt__cphf_r_convergence": 1e-10}
    if ctl:
        del opts["df_basis_elst"]   # the control: Psi4's own SAPT0 defaults (JK-fit electrostatics, dimer basis)
    psi4.set_options(opts)
    r = dict(row=d["row"], geom_id=d["geom_id"], conv=conv, threads=threads, rotated=bool(rot) and not ctl, control=ctl)
    KCAL = r["kcal_psi4"] = psi4.constants.hartree2kcalmol   # recorded: DESRES's Psi4 1.3 used CODATA 2014's
    psi4.energy("sapt0", molecule=mol, sapt_basis="dimer" if ctl else "monomer")
    v = {k: psi4.variable(k) for k in ("SAPT ELST10,R ENERGY", "SAPT EXCH10 ENERGY", "SAPT EXCH10(S^2) ENERGY",
                                       "SAPT IND20,R ENERGY", "SAPT EXCH-IND20,R ENERGY", "SAPT DISP20 ENERGY",
                                       "SAPT SAME-SPIN DISP20 ENERGY", "SAPT EXCH-DISP20 ENERGY",
                                       "SAPT SAME-SPIN EXCH-DISP20 ENERGY", "SAPT HF TOTAL ENERGY",
                                       "SAPT0 TOTAL ENERGY")}
    r["raw_sapt_hartree"] = {k: float(x) for k, x in v.items()}
    s = {"sapt_es": v["SAPT ELST10,R ENERGY"], "sapt_ex": v["SAPT EXCH10 ENERGY"],
         "sapt_exs2": v["SAPT EXCH10(S^2) ENERGY"], "sapt_ind": v["SAPT IND20,R ENERGY"],
         "sapt_exind": v["SAPT EXCH-IND20,R ENERGY"], "sapt_disp": v["SAPT DISP20 ENERGY"],
         "sapt_exdisp_os": v["SAPT EXCH-DISP20 ENERGY"] - v["SAPT SAME-SPIN EXCH-DISP20 ENERGY"],
         "sapt_exdisp_ss": v["SAPT SAME-SPIN EXCH-DISP20 ENERGY"]}
    s["sapt_delta_HF"] = v["SAPT HF TOTAL ENERGY"] - (s["sapt_es"] + s["sapt_ex"] + s["sapt_ind"] + s["sapt_exind"])
    s["sapt_all"] = v["SAPT0 TOTAL ENERGY"]
    r.update({k: float(x) * KCAL for k, x in s.items()})
    r["t_sapt"] = time.time() - t0
    psi4.core.clean()
    psi4.core.clean_variables()
    if not rot:   # neither the rotated copy nor the control
        # CP-corrected DF-HF and DF-MP2: dimer, and each monomer in the dimer basis (the other one ghosted)
        en = {}
        for lab, sub in (("d", mol), ("a", mol.extract_subsets(1, 2)), ("b", mol.extract_subsets(2, 1))):
            psi4.energy("mp2", molecule=sub)
            en[lab] = {k: float(psi4.variable(k)) for k in ("SCF TOTAL ENERGY", "MP2 OPPOSITE-SPIN CORRELATION ENERGY",
                                                            "MP2 SAME-SPIN CORRELATION ENERGY", "MP2 TOTAL ENERGY")}
            psi4.core.clean()
            psi4.core.clean_variables()
        r["raw_mp2_hartree"] = en

        def ie(k):
            return (en["d"][k] - en["a"][k] - en["b"][k]) * KCAL
        r["tz_HF"] = ie("SCF TOTAL ENERGY")
        r["tz_MP2_os"] = ie("MP2 OPPOSITE-SPIN CORRELATION ENERGY")
        r["tz_MP2_ss"] = ie("MP2 SAME-SPIN CORRELATION ENERGY")
        r["tz_MP2_all"] = ie("MP2 TOTAL ENERGY")
    r["t_total"] = time.time() - t0
    r["maxrss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print("RESULT " + json.dumps(r), flush=True)


def rotation(seed):
    """A fixed proper rotation and translation, from a seeded quaternion."""
    import random
    g = random.Random(seed)
    q = [g.gauss(0, 1) for _ in range(4)]
    n = math.sqrt(sum(x * x for x in q))
    w, x, y, z = (c / n for c in q)
    R = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return {"R": R, "t": [g.uniform(-5, 5) for _ in range(3)]}


def job(d, threads, mem, conv, out, rot=None):
    t0 = time.time()
    tag = "%d%s%s" % (d["geom_id"], "-" + conv if conv != "tight" else "",
                      "" if not rot else "-control" if rot.get("control") else "-rot")
    scr = os.path.join(SCRATCH, tag)
    os.makedirs(scr, exist_ok=True)
    env = dict(os.environ, PSI_SCRATCH=scr, OMP_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads),
               MKL_NUM_THREADS=str(threads))
    outfile = os.path.join(out, "outputs", tag + ".out")
    p = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), "--one", json.dumps(d), str(threads), str(mem),
                        conv, outfile, json.dumps(rot)], env=env, capture_output=True, text=True)
    shutil.rmtree(scr, ignore_errors=True)   # this task's own scratch directory, never a staged input
    res = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
    if p.returncode == 0 and res:
        r = json.loads(res[-1][7:])
    else:
        r = dict(row=d["row"], geom_id=d["geom_id"], conv=conv, rotated=bool(rot) and not rot.get("control"),
                 control=bool(rot and rot.get("control")),
                 error="rc=%d %s" % (p.returncode, (p.stderr or p.stdout)[-600:]))
    r["wall"] = time.time() - t0
    return r


def main(argv):
    zpath, member, out, nw, threads, mem, sel, conv = argv[2:10]
    nw, threads, mem = int(nw), int(threads), int(mem)
    os.makedirs(os.path.join(out, "outputs"), exist_ok=True)
    os.makedirs(SCRATCH, exist_ok=True)
    data = load(zpath, member)
    if sel == "all":
        idx = range(len(data))
    elif sel == "pick":   # the smallest, the median and the largest dimer by atom count (the probe)
        bysize = sorted(range(len(data)), key=lambda i: (len(data[i]["atoms"]), i))
        idx = [bysize[0], bysize[len(bysize) // 2], bysize[-1]]
    elif sel == "pick2":
        bysize = sorted(range(len(data)), key=lambda i: (len(data[i]["atoms"]), i))
        idx = [bysize[0], bysize[len(bysize) // 2]]
    else:
        idx = [int(x) for x in sel.split(",")]
    todo = [data[i] for i in idx]
    # two controls on the six smallest dimers: the same geometry rigidly rotated and translated (must not
    # move), and Psi4's default SAPT0 (must not reproduce DESS66, or the bound could not tell them apart)
    small = sorted(todo, key=lambda d: (len(d["atoms"]), d["row"]))[:6 if sel == "all" else 1]
    jobs = [(d, None) for d in sorted(todo, key=lambda d: (-len(d["atoms"]), d["row"]))]
    jobs += [(d, rotation(1000 + d["row"])) for d in small]
    jobs += [(d, {"control": True}) for d in small]
    t0 = time.time()
    print("read %d rows of %s; %d geometries + %d rotated + the same in the control, %d workers x %d threads, %d MB each, conv %s"
          % (len(data), member, len(todo), len(small), nw, threads, mem, conv), flush=True)
    res = []
    with ThreadPoolExecutor(nw) as ex, open(os.path.join(out, "psi4.jsonl"), "w") as fh:
        futs = [ex.submit(job, d, threads, mem, conv, out, rot) for d, rot in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            res.append(r)
            print("%d/%d  %.0f s  row %s  %s" % (i, len(jobs), time.time() - t0, r["row"],
                                                 r.get("error", "%.0f s" % r["wall"])), flush=True)
    return check(res, data, todo, small, time.time() - t0, out, sel == "all")


def check(res, data, todo, small, wall, out, full):
    import check_s66
    return check_s66.check(res, data, todo, small, wall, out, full)


if __name__ == "__main__":
    if sys.argv[1] == "--one":
        one(json.loads(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6],
            json.loads(sys.argv[7]))
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        sys.exit(main(sys.argv))
