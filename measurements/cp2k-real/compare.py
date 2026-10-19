"""Compare two chips' cp2k-real runs.tsv.

    python3 -I compare.py <results/chipA> <results/chipB>

Step 0 of every reference-set run is an SCF converged to EPS_SCF 1e-7 (asserted in real.py) at the
same starting geometry from the same atomic guess: a fixed point. It is ASSERTED to agree within 1e-9 Ha -- the recipe's own chemistry-scale clause
(1 kcal/mol is 1.5936e-3 Ha). Steps 1..10 move the atoms on forces from that SCF, so the final
energy is a path, and is REPORTED. So is H2O-64's step 0: that input sets IGNORE_CONVERGENCE_FAILURE
and its first SCF stops unconverged at MAX_SCF 50, so it is a path too.
"""
import csv
import sys

FIXED_TOL = 1e-9


def load(d):
    with open(d + "/runs.tsv") as f:
        return {r["job"]: r for r in csv.DictReader(f, delimiter="\t")}


a, b = load(sys.argv[1]), load(sys.argv[2])
bad = 0
print("%-26s %14s %14s %14s %8s" % ("job", "step0 diff", "final diff", "cons_final", "wall b/a"))
worst0 = 0.0
for job in a:
    if job not in b:
        print("%-26s missing on %s" % (job, sys.argv[2]))
        bad += 1
        continue
    x, y = a[job], b[job]
    d0 = float(y["e_step0"]) - float(x["e_step0"])
    df = float(y["e_final"]) - float(x["e_final"])
    dc = float(y["md_cons_final"]) - float(x["md_cons_final"])
    fixed = x["kind"] == "ref"
    if fixed:
        worst0 = max(worst0, abs(d0))
    ok = (not fixed) or abs(d0) <= FIXED_TOL     # a NaN fails here too
    print("%-26s %14.3e %14.3e %14.3e %8.2f%s" % (job, d0, df, dc, float(y["wall_s"]) / float(x["wall_s"]),
                                                  "" if ok else "  FAIL step 0 > 1e-9 Ha"))
    bad += not ok
print("worst step-0 |diff| %.3e Ha over the %d reference-set runs (asserted <= 1e-9)"
      % (worst0, sum(r["kind"] == "ref" for r in a.values())))
if bad:
    print("FAILED: %d" % bad)
    sys.exit(1)
print("fixed point agrees")
