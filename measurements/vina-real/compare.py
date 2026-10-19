"""Cross-chip comparison of three vina-real runs: fixed points asserted, paths reported.

    python3 -I compare.py <results-dir>        # results/{arm64,x86_64,amd}/results.tsv

Fixed point (asserted): the crystal pose's score, one evaluation of the same function at the same
coordinates on the same grid maps. Vina returns energies rounded to 0.001 kcal/mol, so "within 1e-6"
means the same reported value. Reported: the crystal pose's local optimisation (a short minimisation,
whose iterates a different build can move) and everything the seeded Monte Carlo search returns.
"""
import sys

D = sys.argv[1]
CHIPS = ["arm64", "x86_64", "amd"]
NAME = {"arm64": "Graviton4", "x86_64": "Intel", "amd": "AMD"}


def load(chip):
    rows = open("%s/%s/results.tsv" % (D, chip)).read().splitlines()
    h = rows[0].split("\t")
    out = {}
    for ln in rows[1:]:
        r = dict(zip(h, ln.split("\t")))
        out[r["complex"]] = r
    return out


R = {c: load(c) for c in CHIPS}
fail = []
ids = sorted(R["arm64"])
print("complexes per chip:", {c: len(R[c]) for c in CHIPS})
if any(sorted(R[c]) != ids for c in CHIPS):
    fail.append("same complex set")
for base, chip in (("arm64", "x86_64"), ("arm64", "amd"), ("x86_64", "amd")):
    a, b = R[base], R[chip]
    print("\n== %s vs %s" % (NAME[base], NAME[chip]))
    for k, tol in (("e_xtal", 1e-6), ("e_xtal_opt", None), ("rmsd_xtal_opt", None)):
        d = [abs(float(a[i][k]) - float(b[i][k])) for i in ids]
        same = sum(x == 0.0 for x in d)
        line = "%-16s identical %d/%d  max|diff| %.3g" % (k, same, len(ids), max(d))
        if tol is not None:
            ok = max(d) <= tol
            line += "  want <= %g  %s" % (tol, "ok" if ok else "FAIL")
            if not ok:
                fail.append("%s/%s %s" % (base, chip, k))
        print(line)
    for k in ("e_top", "rmsd_top", "pose_sha"):
        same = sum(a[i][k] == b[i][k] for i in ids)
        print("%-16s identical %d/%d (path, reported)" % (k, same, len(ids)))
    d = [float(b[i]["e_top"]) - float(a[i]["e_top"]) for i in ids]
    print("e_top %s minus %s: mean %+.4f  min %+.3f  max %+.3f" % (NAME[chip], NAME[base], sum(d) / len(d), min(d), max(d)))
    flips = sum((float(a[i]["rmsd_top"]) <= 2) != (float(b[i]["rmsd_top"]) <= 2) for i in ids)
    print("top-1 <= 2 A verdict differs on %d complexes" % flips)
print()
if fail:
    print("FAILED:", ", ".join(fail))
    sys.exit(1)
print("CROSS-CHIP FIXED POINTS OK")
