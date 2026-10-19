"""Compare the three chips' outputs against Graviton4. Every quantity here is a fixed point, so it
must agree: space groups, self-matches, stability flags, decomposition product sets and the merged
material groups exactly; hull distances, formation energies and decomposition energies and amounts
within 1e-9 (eV/atom, or atomic fraction), except a decomposition enthalpy pymatgen finds by SLSQP
(ftol 1e-8; whenever a same-composition stable entry exists) and is bounded at 1e-6 eV/atom, as against MP. The 1e-9 bound is ~1e5 x the double-precision rounding of a
~10 eV/atom energy and 1e6 x below the 1 meV/atom anyone reads a hull distance to; it was fixed
before the x86 runs.

    python3 collect.py <dir with arm64/ x86_64/ amd/, each holding a run's untarred out/ files>
"""
import math
import os
import sys

D = sys.argv[1]
CHIPS = [c for c in ("arm64", "x86_64", "amd") if os.path.isdir(os.path.join(D, c))]
TOL = 1e-9
fail = []


def rows(chip, name):
    with open(os.path.join(D, chip, name)) as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        return hdr, {l.split("\t", 1)[0]: l.rstrip("\n").split("\t") for l in fh if l.strip()}


def num(x):
    return float(x)


def report(label, chip, ok, detail):
    print("%-44s %-7s %s  %s" % (label, chip, "ok  " if bool(ok) else "FAIL", detail))
    if not bool(ok):
        fail.append("%s %s" % (chip, label))


for chip in CHIPS[1:]:
    # symmetry + self-match: integers and booleans, exact
    _, a = rows("arm64", "symmetry.tsv")
    _, b = rows(chip, "symmetry.tsv")
    diff = [k for k in a if a[k] != b.get(k)]
    report("symmetry.tsv rows identical", chip, len(a) == len(b) and not diff,
           "%d rows, %d differ %s" % (len(a), len(diff), diff[:3]))
    for tt in ("GGA_GGAUU", "R2SCAN"):
        name = "hull-%s.tsv" % tt
        hdr, a = rows("arm64", name)
        _, b = rows(chip, name)
        ix = {h: i for i, h in enumerate(hdr)}
        worst = {k: 0.0 for k in ("ehull", "ef", "decomp_enthalpy", "eq_rxn", "amount", "dH_slsqp")}
        nflag = 0
        for k, ra in a.items():
            rb = b.get(k)
            if rb is None:
                nflag += 1
                continue
            for col in ("ehull", "ef", "decomp_enthalpy", "eq_rxn"):
                x, y = num(ra[ix[col]]), num(rb[ix[col]])
                if math.isnan(x) and math.isnan(y):
                    continue
                d = abs(x - y) if not (math.isnan(x) or math.isnan(y)) else float("inf")
                if col == "decomp_enthalpy" and ra[ix["dH_slsqp"]] == "1":
                    col = "dH_slsqp"
                worst[col] = max(worst[col], d)
            if ra[ix["stable"]] != rb[ix["stable"]]:
                nflag += 1
            da = [p.split(":") for p in ra[ix["decomp"]].split(";") if p]
            db = [p.split(":") for p in rb[ix["decomp"]].split(";") if p]
            if [m for m, _ in da] != [m for m, _ in db]:
                nflag += 1
            else:
                for (_, x), (_, y) in zip(da, db):
                    worst["amount"] = max(worst["amount"], abs(float(x) - float(y)))
        report("%s stability + decomposition sets identical" % tt, chip,
               len(a) == len(b) and nflag == 0, "%d rows, %d differ" % (len(a), nflag))
        report("%s numbers vs Graviton4 (1e-9; SLSQP 1e-6)" % tt, chip, all(v <= (1e-6 if k == "dH_slsqp" else TOL) for k, v in worst.items()),
               "max |d| " + ", ".join("%s %.2e" % kv for kv in worst.items()))
    ma = open(os.path.join(D, "arm64", "matches.tsv")).read()
    mb = open(os.path.join(D, chip, "matches.tsv")).read()
    report("matches.tsv identical", chip, ma == mb, "%d merged groups" % (ma.count("\n") - 1))
print("FAILED: " + ", ".join(fail) if fail else "ALL CROSS-CHIP FIXED POINTS AGREE")
sys.exit(1 if fail else 0)
