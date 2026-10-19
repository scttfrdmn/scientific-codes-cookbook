"""Compare the three chips' AUSURF112 runs against Graviton4. The converged energy and forces are
fixed points and are asserted with check.py's own bounds; iteration counts and wall times are a path
and are reported. Writes <dir>/crosschip.txt; exits non-zero on a failure.
    collect.py <dir holding arm64/ x86_64/ amd/, each a run's out/ files>"""
import csv
import os
import sys

R = sys.argv[1]
CHIPS = ["arm64", "x86_64", "amd"]
TOL_E = 100 * 1e-8          # Ry, check.py's decomposition bound; same reasoning across chips


def load(chip):
    d = os.path.join(R, chip)
    rows = {r["run"]: r for r in csv.DictReader(open(os.path.join(d, "results.tsv")), delimiter="\t")}
    forces = {t: [tuple(map(float, l.split("\t")[1:4])) for l in open(os.path.join(d, "forces-%s.tsv" % t))]
              for t in ("A", "B")}
    return rows, forces


data = {c: load(c) for c in CHIPS}
fail, out = [], []


def line(s):
    out.append(s)
    print(s)


ref_rows, ref_f = data["arm64"]
for c in CHIPS[1:]:
    rows, forces = data[c]
    for t in ("A", "B"):
        dE = abs(float(rows[t]["E_ry"]) - float(ref_rows[t]["E_ry"]))
        ok = bool(dE < TOL_E)
        line("%-7s %s  |E - E_Graviton4| = %.2e Ry (want < %.0e)  [%s]" % (c, t, dE, TOL_E, "ok" if ok else "FAIL"))
        fail += [] if ok else ["%s %s energy" % (c, t)]
        dF = max(abs(a - b) for x, y in zip(forces[t], ref_f[t]) for a, b in zip(x, y))
        bound = float(rows[t]["scf_corr"]) + float(ref_rows[t]["scf_corr"])
        ok = bool(len(forces[t]) == len(ref_f[t]) == 112 and dF <= bound)
        line("%-7s %s  max|F - F_Graviton4| = %.2e Ry/au (want <= SCF corrections %.2e)  [%s]"
             % (c, t, dF, bound, "ok" if ok else "FAIL"))
        fail += [] if ok else ["%s %s forces" % (c, t)]
        line("%-7s %s  Fermi %s vs %s eV, iterations %s vs %s  [reported: path]"
             % (c, t, rows[t]["ef_ev"], ref_rows[t]["ef_ev"], rows[t]["iters"], ref_rows[t]["iters"]))
line("")
line("chip     run  E (Ry)              iterations  scf accuracy  wall")
for c in CHIPS:
    for t in ("A", "B"):
        r = data[c][0][t]
        line("%-8s %-4s %-19s %-11s %-13s %s" % (c, t, r["E_ry"], r["iters"], r["scf_acc"], r["wall"]))
line("CROSS-CHIP PASSED" if not fail else "CROSS-CHIP FAILED %s" % fail)
open(os.path.join(R, "crosschip.txt"), "w").write("\n".join(out) + "\n")
sys.exit(1 if fail else 0)
