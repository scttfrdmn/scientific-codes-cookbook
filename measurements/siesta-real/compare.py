#!/usr/bin/env python3
"""Compare two chips' siesta-real outputs.

    python3 -I compare.py <results/chipA> <results/chipB>

Suite: every digest value (upstream's out_digest_yaml.awk: the last value of each quantity the
output prints) of every test both chips ran, A minus B, in three groups:
  converged    -- every SCF converged and there is no trajectory: a fixed point
  trajectory   -- 08 geometry optimisation, 09 molecular dynamics, 15 TDDFT, SCFs converged: a path
  unconverged  -- SIESTA printed SCF_NOT_CONV on either chip: a point on the SCF path
Reported, not asserted: the suite's per-test claim is against its reference, on each chip.

Quantum dot: the converged Si987H372 total energies are fixed points, and the two chips must agree
within upstream's own Etot tolerance (test-cfg.yml, 0.003 eV) -- the bound the suite applies when
it compares one build against a reference from another. Asserted.
"""
import csv
import sys

PATH_DIRS = {"08.GeometryOptimization", "09.MolecularDynamics", "15.TDDFT"}
ETOL = 0.003


def load(d, f):
    return list(csv.DictReader(open("%s/%s" % (d, f)), delimiter="\t"))


a, b = sys.argv[1], sys.argv[2]
sa = {(r["dir"], r["test"]): r for r in load(a, "suite.tsv")}
sb = {(r["dir"], r["test"]): r for r in load(b, "suite.tsv")}
da = {(r["dir"], r["test"], r["section"], r["key"]): float(r["value"]) for r in load(a, "digest.tsv")}
db = {(r["dir"], r["test"], r["section"], r["key"]): float(r["value"]) for r in load(b, "digest.tsv")}


def group(t):
    if sa[t]["scf_not_conv"] == "True" or sb[t]["scf_not_conv"] == "True":
        return "unconverged"
    return "trajectory" if t[0] in PATH_DIRS else "converged"


common = sorted(set(da) & set(db))
both = sorted(set(sa) & set(sb))
print("tests on both: %d (only on A: %d, only on B: %d)" % (len(both), len(set(sa) - set(sb)), len(set(sb) - set(sa))))
for g in ("converged", "trajectory", "unconverged"):
    tests = [t for t in both if group(t) == g]
    ks = [k for k in common if k[:2] in tests and (da[k] != 0.0 or db[k] != 0.0)]
    same = sum(da[k] == db[k] for k in ks)
    dt = sorted({k[:2] for k in ks if da[k] != db[k]})
    print("%-11s %3d tests, %4d non-zero values, %4d identical to the printed digit, %d tests differ anywhere"
          % (g, len(tests), len(ks), same, len(dt)))
    e = [k for k in ks if k[2] == "energies"]
    if e:
        w = max(e, key=lambda k: abs(da[k] - db[k]))
        print("            largest energy |diff| %.6f eV  (%s/%s %s)" % (abs(da[w] - db[w]), w[0], w[1], w[3]))
        et = [k for k in e if k[3] == "Etot"]   # variational: stationary at the fixed point, its parts are not
        w = max(et, key=lambda k: abs(da[k] - db[k]))
        print("            largest Etot   |diff| %.6f eV  (%s/%s)" % (abs(da[w] - db[w]), w[0], w[1]))
vd = [t for t in both if sa[t]["verify"] != sb[t]["verify"]]
print("verify verdict differs in %d tests%s" % (len(vd), "".join(
    "\n  %s/%s (%s): %s vs %s" % (t[0], t[1], group(t), sa[t]["verify"], sb[t]["verify"]) for t in vd)))

qa = {r["run"]: r for r in load(a, "qdot.tsv")}
qb = {r["run"]: r for r in load(b, "qdot.tsv")}
fail = False
for run in sorted(set(qa) & set(qb)):
    ea, eb = float(qa[run]["Etot_eV"]), float(qb[run]["Etot_eV"])
    print("qdot %-20s Etot %.6f vs %.6f  diff %+.6f eV  scf steps %s vs %s" % (
        run, ea, eb, ea - eb, qa[run]["scf_steps"], qb[run]["scf_steps"]))
    if qa[run]["converged"] == "True" and not abs(ea - eb) < ETOL:
        fail = True
        print("FAIL: converged %s Etot differs by more than %.3f eV" % (run, ETOL))
sys.exit(1 if fail else 0)
