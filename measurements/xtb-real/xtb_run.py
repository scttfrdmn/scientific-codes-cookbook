"""GFN2-xTB over all of GMTKN55: every structure, every reaction, and the gradient checked against the
energy it claims to be the derivative of.

    python3 -u xtb_run.py <GMTKN55 dir> <out dir> <workers> [limit]

Per structure (one xtb process per worker, one thread each):
  bench  xtb --gfn 2 --etemp 300 --acc 1.0 --grad        the benchmark energy (xtb's defaults, explicit)
  tight  the same at --acc 0.0001                         E0 and the analytic gradient g
  fd     E(x0 + k h d) for k = -2, -1, +1, +2 at --acc 0.0001, d a seeded random unit vector in R^3N
Writes <out>/xtb.jsonl (one record per structure, gradients included) and <out>/check.txt, and exits 1
if any assertion fails. It does not know which chip it is on: a wrong gradient fails here as anywhere.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zlib
from multiprocessing import Pool

import numpy as np

KCAL = 627.509474   # kcal/mol per Hartree (CODATA 2018), the conversion GMTKN55's tmer2++ applies
H = 1.0e-3          # finite-difference step along d, Bohr (norm of the whole 3N displacement)
SETTINGS = ["--gfn", "2", "--etemp", "300.0"]
WORK = "/tmp/w"
FD_ABS, FD_REL = 1e-7, 1e-5   # analytic vs 5-point FD bound (see check)
TORQUE = 1e-7                 # |sum r x g| of a rotation-invariant energy, Eh (Bohr cancels)

# arXiv:2107.00299v2 (Christensen et al., OrbNet Denali), Table IV, column "GFN2-xTB": MAE in kcal/mol
# against GMTKN55's own references, over the 42 subsets whose molecules are all singlets (Table VI).
PUBLISHED = {
    "DIPCS10": 274.75, "PA26": 163.05, "AL2X6": 14.63, "NBPRC": 10.51, "ALK8": 23.91, "G2RC": 21.92,
    "FH51": 11.41, "TAUT15": 0.98, "DARC": 17.77, "BSR36": 2.76, "CDIE20": 1.80, "ISO34": 6.90,
    "ISOL24": 11.68, "C60ISO": 5.80, "PArel": 5.86, "BHPERI": 10.24, "BHDIV10": 8.12, "INV24": 3.32,
    "BHROT27": 1.17, "PX13": 2.74, "WCPT18": 3.84, "RG18": 0.11, "ADIM6": 1.15, "S22": 0.76,
    "S66": 0.73, "HEAVY28": 0.61, "WATER27": 3.05, "CARBHB12": 1.79, "PNICO23": 1.11, "HAL59": 1.28,
    "AHB21": 2.97, "CHB6": 5.40, "IL16": 4.32, "IDISP": 6.78, "ICONF": 1.63, "ACONF": 0.19,
    "Amino20x4": 0.95, "PCONF21": 1.76, "MCONF": 1.72, "SCONF": 1.64, "UPU23": 2.91, "BUT14DIOL": 1.25,
}
PUBLISHED_WTMAD2 = 18.89
# Measured on the first Intel run and asserted from then on, on every chip:
#   CARBHB12  one of its 12 reactions needs CARBHB12/1N, whose SCC does not converge here (11 of 12 averaged)
#   WATER27   3.15 vs 3.05, NBPRC 10.53 vs 10.51: not convergence (acc 1e-4 gives the same MAE); the paper
#             does not state its xtb version, so a release difference cannot be ruled out
PUBLISHED_DIFFER = {"CARBHB12", "WATER27", "NBPRC"}
SCF_FAIL = {"CARBHB12/1N", "G21IP/b+", "G21IP/be+", "G21IP/c+"}


def read_int(path, default=0):
    try:
        with open(path) as fh:
            return int(fh.read().split()[0])
    except (OSError, IndexError, ValueError):
        return default


def read_coord(path):
    """Turbomole $coord block: (positions in Bohr as an (N,3) array, element tokens)."""
    xyz, el, on = [], [], False
    with open(path) as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("$"):
                on = s.startswith("$coord")
                continue
            if on and s:
                t = s.split()
                xyz.append([float(v.replace("D", "E").replace("d", "e")) for v in t[:3]])
                el.append(t[3])
    return np.array(xyz), el


def write_coord(path, xyz, el):
    with open(path, "w") as fh:
        fh.write("$coord\n")
        for (x, y, z), e in zip(xyz, el):
            fh.write("%24.16f%24.16f%24.16f  %s\n" % (x, y, z, e))
        fh.write("$end\n")


def run(cmd, cwd):
    """Run one process; return (rc, wall s, peak RSS KiB of that process, stdout text)."""
    t0 = time.time()
    with open(os.path.join(cwd, "out.txt"), "w") as out:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
        _, status, ru = os.wait4(p.pid, 0)
    rc = os.waitstatus_to_exitcode(status)
    with open(os.path.join(cwd, "out.txt"), errors="replace") as fh:
        text = fh.read()
    return rc, time.time() - t0, ru.ru_maxrss, text


def energy_of(cwd, text):
    """Total energy from the summary block, which prints 12 decimals (xtbout.json prints only 8)."""
    m = re.findall(r"TOTAL ENERGY\s+(-?\d+\.\d+)\s+Eh", text)
    return float(m[-1]) if m else None


def gradient_of(cwd, n):
    """The Cartesian gradient from xtb's Turbomole `gradient` file (the last $grad cycle)."""
    rows = []
    with open(os.path.join(cwd, "gradient")) as fh:
        for line in fh:
            t = line.split()
            if len(t) == 3:
                try:
                    rows.append([float(v.replace("D", "E").replace("d", "e")) for v in t])
                except ValueError:
                    pass
    return np.array(rows[-n:]) if len(rows) >= n else None


def xtb(tag, sysdir, coordsrc, chrg, uhf, acc, grad, xyz=None, el=None):
    d = os.path.join(WORK, sysdir.replace("/", "__") + "." + tag)
    os.makedirs(d, exist_ok=True)
    if xyz is None:
        shutil.copyfile(coordsrc, os.path.join(d, "coord"))
    else:
        write_coord(os.path.join(d, "coord"), xyz, el)
    cmd = ["xtb", "coord"] + SETTINGS + ["--acc", acc, "--chrg", str(chrg), "--uhf", str(uhf)]
    if grad:
        cmd.append("--grad")
    rc, wall, rss, text = run(cmd, d)
    r = {"rc": rc, "wall": wall, "rss_kib": rss, "E": energy_of(d, text),
         "converged": bool(re.search(r"SCC converged|convergence criteria satisfied", text)),
         "scc_failed": bool(re.search(r"SCC.*not converge|SCF not converged", text, re.I))}
    if grad and rc == 0:
        r["g"] = gradient_of(d, len(el) if el else read_coord(coordsrc)[0].shape[0])
    if rc != 0:
        r["tail"] = text[-600:]
    shutil.rmtree(d, ignore_errors=True)   # a directory this task built, never a staged input
    return r


def tblite(sysdir, coordsrc, chrg, uhf, xyz=None, el=None, tag="tblite"):
    d = os.path.join(WORK, sysdir.replace("/", "__") + "." + tag)
    os.makedirs(d, exist_ok=True)
    if xyz is None:
        shutil.copyfile(coordsrc, os.path.join(d, "coord"))
    else:
        write_coord(os.path.join(d, "coord"), xyz, el)
    cmd = ["tblite", "run", "--method", "gfn2", "--etemp", "300.0", "--acc", "0.0001", "--charge", str(chrg),
           "--spin", str(uhf), "--grad", "tblite.txt", "--json", "tblite.json", "coord"]
    rc, wall, rss, text = run(cmd, d)
    r = {"rc": rc, "wall": wall, "rss_kib": rss, "E": None, "g": None}
    try:
        with open(os.path.join(d, "tblite.json")) as fh:
            j = json.load(fh)
        r["E"] = float(j["energy"])
        if "gradient" in j:
            r["g"] = np.array(j["gradient"], dtype=float).reshape(-1, 3)
    except (OSError, KeyError, ValueError) as e:
        r["err"] = "%s: %s" % (type(e).__name__, e)
        r["tail"] = text[-600:]
    shutil.rmtree(d, ignore_errors=True)
    return r


def one(job):
    sysdir, root = job
    src = os.path.join(root, sysdir, "coord")
    xyz, el = read_coord(src)
    chrg = read_int(os.path.join(root, sysdir, ".CHRG"))
    uhf = read_int(os.path.join(root, sysdir, ".UHF"))
    n = len(el)
    rec = {"sys": sysdir, "natoms": n, "chrg": chrg, "uhf": uhf, "elements": sorted(set(e.lower() for e in el))}
    t0 = time.time()
    b = xtb("bench", sysdir, src, chrg, uhf, "1.0", True)
    rec["bench"] = {k: b.get(k) for k in ("rc", "wall", "rss_kib", "E", "converged", "scc_failed", "tail")}
    if b.get("g") is not None:
        rec["bench"]["gnorm"] = float(np.linalg.norm(b["g"]))
    t = xtb("tight", sysdir, src, chrg, uhf, "0.0001", True)
    rec["tight"] = {k: t.get(k) for k in ("rc", "wall", "rss_kib", "E", "converged", "scc_failed", "tail")}
    g = t.get("g")
    if g is not None:
        rec["tight"]["g"] = g.ravel().tolist()
        rec["tight"]["sum_g"] = float(np.abs(g.sum(axis=0)).max())
        c = xyz - xyz.mean(axis=0)
        rec["tight"]["torque"] = float(np.abs(np.cross(c, g).sum(axis=0)).max())
    if n > 1 and g is not None:
        rng = np.random.default_rng(zlib.crc32(sysdir.encode()))
        dvec = rng.standard_normal(xyz.shape)
        dvec /= np.linalg.norm(dvec)
        es = {}
        for k in (-2, -1, 1, 2):
            r = xtb("fd%+d" % k, sysdir, src, chrg, uhf, "0.0001", False, xyz + k * H * dvec, el)
            es[k] = r["E"] if r["rc"] == 0 else None
        if all(v is not None for v in es.values()):
            d1 = (es[1] - es[-1]) / (2 * H)                                   # O(h^2)
            d2 = (es[2] - es[-2]) / (4 * H)
            d4 = (8 * (es[1] - es[-1]) - (es[2] - es[-2])) / (12 * H)          # O(h^4)
            rec["fd"] = {"analytic": float((g * dvec).sum()), "central": d1, "central_2h": d2, "five_point": d4,
                         "E": [es[-2], es[-1], es[1], es[2]]}
    rec["wall"] = time.time() - t0
    return rec


def parse_res(root, subset, fname=".res"):
    """GMTKN55's tmer2++ lines: (species dirs, stoichiometry, reference kcal/mol) per reaction."""
    out = []
    with open(os.path.join(root, subset, fname)) as fh:
        for line in fh:
            t = line.split()
            if not t or t[0] not in ("$tmer", "tmer2++") or "x" not in t:
                continue
            ix, iw = t.index("x"), t.index("$w")
            dirs = []
            for tok in t[1:ix]:
                tok = tok.replace("/$f", "")
                m = re.match(r"^(.*)\{([^}]*)\}(.*)$", tok)
                dirs += [m.group(1) + a + m.group(3) for a in m.group(2).split(",")] if m else [tok]
            coef = [float(v) for v in t[ix + 1:iw]]
            assert len(coef) == len(dirs), (subset, line)
            out.append((dirs, coef, float(t[iw + 1])))
    return out


def main():
    root, outdir, nw = sys.argv[1], sys.argv[2], int(sys.argv[3])
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    with open(os.path.join(root, ".list.dirs")) as fh:
        subsets = fh.read().split()
    systems = sorted(os.path.relpath(dp, root) for dp, _, fs in os.walk(root) if "coord" in fs)
    print("subsets", len(subsets), "structures", len(systems), flush=True)
    jobs = [(s, root) for s in systems]
    if limit:
        jobs = jobs[:limit]
    # the biggest structures first, so the pool does not end on one long straggler
    jobs.sort(key=lambda j: -read_coord(os.path.join(root, j[0], "coord"))[0].shape[0])
    recs = {}
    t0 = time.time()
    with Pool(nw) as pool, open(os.path.join(outdir, "xtb.jsonl"), "w") as fh:
        for i, r in enumerate(pool.imap_unordered(one, jobs), 1):
            recs[r["sys"]] = r
            fh.write(json.dumps(r) + "\n")
            if i % 200 == 0:
                print("done %d/%d  %.0f s" % (i, len(jobs), time.time() - t0), flush=True)
    print("structures_wall_s\t%.1f" % (time.time() - t0), flush=True)
    sys.exit(check(root, subsets, systems, recs, outdir, full=not limit))


def check(root, subsets, systems, recs, outdir, full):
    lines, fails = [], []

    def rec(name, value, ok=None):
        lines.append("%s\t%s\t%s" % (name, value, "" if ok is None else ("ok" if bool(ok) else "FAIL")))
        if ok is not None and not bool(ok):
            fails.append(name)

    R = list(recs.values())
    rec("structures_run", len(R), len(R) == 2462 if full else None)
    # GFN2's SCC at xtb's defaults does not converge on four structures (three atomic cations and one
    # CARBHB12 complex); xtb exits non-zero and writes no energy, so they are named, not averaged over.
    bad = sorted(r["sys"] for r in R if r["bench"]["rc"] != 0 or r["bench"]["E"] is None)
    rec("bench_scf_not_converged", "%d %s" % (len(bad), " ".join(bad[:20])), set(bad) <= SCF_FAIL if full else None)
    badt = sorted(r["sys"] for r in R if r["tight"]["rc"] != 0 or r["tight"]["E"] is None)
    rec("tight_scf_not_converged", "%d %s" % (len(badt), " ".join(badt[:20])), set(badt) <= SCF_FAIL if full else None)
    for k in bad:
        t = recs[k]["bench"].get("tail") or ""
        rec("nonconverged_exit_is_xtb_scc_error_" + k, recs[k]["bench"]["rc"], "did not converge" in t)
    shift = [abs(r["tight"]["E"] - r["bench"]["E"]) for r in R if r["tight"]["E"] is not None and r["bench"]["E"] is not None]
    rec("bench_vs_tight_max_abs_Eh", "%.3e" % max(shift) if shift else "nan")

    # ---- GMTKN55: every reaction, per-subset MAE, and the 42 published GFN2-xTB MAEs
    E = {r["sys"]: r["bench"]["E"] for r in R}
    rows, mad = [], {}
    for s in subsets:
        for fname, label in ((".res", s), (".resRC", "BH76RC")):
            if not os.path.exists(os.path.join(root, s, fname)):
                continue
            errs = []
            for dirs, coef, ref in parse_res(root, s, fname):
                keys = [s + "/" + d for d in dirs]
                if any(E.get(k) is None for k in keys):
                    errs.append(None)
                    continue
                v = KCAL * sum(c * E[k] for c, k in zip(coef, keys))
                errs.append(v - ref)
                rows.append("%s\t%s\t%.6f\t%.3f" % (label, "+".join(dirs), v, ref))
            ok = [e for e in errs if e is not None]
            mad[label] = (sum(abs(e) for e in ok) / len(ok) if ok else float("nan"), len(ok), len(errs))
    with open(os.path.join(outdir, "reactions.tsv"), "w") as fh:
        fh.write("subset\tspecies\tgfn2_kcal\tref_kcal\n" + "\n".join(rows) + "\n")
    nre = sum(m[2] for m in mad.values())
    rec("reactions", "%d in %d subsets" % (nre, len(mad)), nre == 1505 and len(mad) == 55 if full else None)
    with open(os.path.join(outdir, "subsets.tsv"), "w") as fh:
        fh.write("subset\tn\tmae_kcal\tpublished\tdiff\n")
        worst = (0.0, "")
        for k in sorted(mad):
            p = PUBLISHED.get(k)
            d = abs(round(mad[k][0], 2) - p) if p is not None else None
            fh.write("%s\t%d\t%.4f\t%s\t%s\n" % (k, mad[k][1], mad[k][0], "" if p is None else "%.2f" % p,
                                                 "" if d is None else "%.2f" % d))
            if d is not None and d > worst[0]:
                worst = (d, k)
    if full:
        # Table IV prints to 0.01 kcal/mol, so an identical GFN2 reproduces every row after rounding.
        diffs = {k: abs(round(mad[k][0], 2) - p) for k, p in PUBLISHED.items()}
        same = sorted(k for k, v in diffs.items() if v < 0.005)
        rest = sorted(k for k in diffs if k not in same)
        rec("published_mae_reproduced_at_print_precision", "%d of %d; differ: %s" % (
            len(same), len(PUBLISHED), " ".join("%s %.2f vs %.2f" % (k, mad[k][0], PUBLISHED[k]) for k in rest)),
            set(rest) <= PUBLISHED_DIFFER)
        # WTMAD-2 over those 42 subsets, GMTKN55's weighting with the paper's average (56.84)
        num = den = 0.0
        for k in PUBLISHED:
            with open(os.path.join(root, "BH76" if k == "BH76RC" else k, ".averageRC" if k == "BH76RC" else ".average")) as fh:
                av = float(fh.read().split()[0])
            n = mad[k][2]
            num += n * (56.84 / av) * mad[k][0]
            den += n
        # Reported only: GMTKN55's WTMAD-2 formula applied to Table IV's own MAEs gives 20.17, not the
        # 18.89 the table prints, so the paper's weighting is not the published one and cannot be matched.
        rec("wtmad2_42_subsets", "%.2f (Table IV prints %.2f; its own MAEs under this formula give 20.17)" % (num / den, PUBLISHED_WTMAD2))

    # ---- the gradient is the derivative of the energy: a seeded random direction, central and 5-point FD
    fd = [r for r in R if "fd" in r]
    rec("fd_structures", "%d of %d multi-atom" % (len(fd), sum(1 for r in R if r["natoms"] > 1)))
    # FD_ABS / FD_REL: the 5-point estimate's own error. SCF noise at acc 1e-4 (energies to ~1e-10 Eh over
    # 12 h) is ~1e-9 Eh/Bohr, truncation O(h^4) ~1e-12; correct gradients measured within 2.2e-7 relative.
    worst_fd, bad_fd, ratio, errs = 0.0, [], [], []
    for r in fd:
        f = r["fd"]
        err = abs(f["analytic"] - f["five_point"])
        rel = err / max(abs(f["five_point"]), 1e-12)
        errs.append(rel)
        if err > FD_ABS + FD_REL * abs(f["five_point"]):
            bad_fd.append(r["sys"])
        e1, e2 = abs(f["central"] - f["five_point"]), abs(f["central_2h"] - f["five_point"])
        if e1 > 1e-8:
            ratio.append(e2 / e1)
    errs = np.array(errs)
    rec("fd_rel_err_quantiles_50_90_99_max", " ".join("%.2e" % v for v in np.percentile(errs, [50, 90, 99, 100])))
    for t in (1e-6, 1e-4, 1e-2, 1.0):
        rec("fd_rel_err_gt_%g" % t, int((errs > t).sum()))
    rec("fd_gradient_is_derivative_of_energy", "%d of %d fail %s" % (len(bad_fd), len(fd), " ".join(sorted(bad_fd)[:12])), not bad_fd)
    with open(os.path.join(outdir, "gradient-fail.txt"), "w") as fh:
        fh.write("\n".join(sorted(bad_fd)) + "\n")
    if ratio:
        rec("fd_truncation_ratio_2h_over_h_median", "%.2f (O(h^2) predicts 4)" % float(np.median(ratio)))
    sg = [r["tight"]["sum_g"] for r in R if "sum_g" in r["tight"]]
    tq = [(r["tight"]["torque"], r["sys"]) for r in R if "torque" in r["tight"]]
    rec("translation_max_abs_sum_g", "%.3e" % max(sg), max(sg) < 1e-8)
    badtq = sorted(k for v, k in tq if v > TORQUE)
    rec("rotation_torque_gt_%g" % TORQUE, "%d of %d, max %.3e" % (len(badtq), len(tq), max(tq)[0]), not badtq)

    with open(os.path.join(outdir, "check.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.write("RESULT\t%s\n" % ("FAIL: " + " ".join(fails) if fails else "all assertions held"))
    print("\n".join(lines))
    print("RESULT", "FAIL: " + " ".join(fails) if fails else "all assertions held", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    main()
