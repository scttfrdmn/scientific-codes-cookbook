#!/usr/bin/env python3
"""openfoam-real cross-chip comparison: Intel and AMD against Graviton4, from results/<chip>/.

    python3 measurements/openfoam-real/compare.py [dir]    # dir/<chip>/ = the bucket prefixes, copied down

Fixed point: final CL, CD at each angle must agree with Graviton4's within 10x the last
1000-iteration change (the iterative-error argument su2-real uses). Path: the coefficient and
residual histories are reported -- the first iteration they differ, and the largest difference.
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "results")
CHIPS = ["arm64", "x86_64", "amd"]


def results(chip):
    with open(os.path.join(ROOT, chip, "results.tsv")) as fh:
        return {r["alpha"]: r for r in csv.DictReader(fh, delimiter="\t")}


def history(chip, a):
    rows, head = [], None
    with open(os.path.join(ROOT, chip, "a%s.coeffs.dat" % a)) as fh:
        for line in fh:
            if line.startswith("#"):
                f = line[1:].split()
                if f and f[0] == "Time":
                    head = f
                continue
            v = line.split()
            if v and head:
                rows.append(dict(zip(head, map(float, v))))
    return rows


def residuals(chip, a):
    out = []
    for name in ("a%s.log.rhoSimpleFoam.1" % a, "a%s.log.rhoSimpleFoam" % a):
        with open(os.path.join(ROOT, chip, name)) as fh:
            for line in fh:
                if "Solving for p," in line:
                    out.append(line.split("Initial residual = ")[1].split(",")[0])
    return out


def work(chip, a):
    """SIMPLE iterations, GAMG p-solve iterations and solver seconds, from both stage logs."""
    it = gi = 0
    ex = 0.0
    for name in ("a%s.log.rhoSimpleFoam.1" % a, "a%s.log.rhoSimpleFoam" % a):
        last = 0.0
        with open(os.path.join(ROOT, chip, name)) as fh:
            for line in fh:
                if line.startswith("Time = "):
                    it += 1
                elif "GAMG:  Solving for p" in line:
                    gi += int(line.rsplit(" ", 1)[1])
                elif line.startswith("ExecutionTime"):
                    last = float(line.split()[2])
        ex += last
    return it, gi, ex


def partition(chip, a):
    with open(os.path.join(ROOT, chip, "a%s.partition.txt" % a)) as fh:
        return fh.read()


fail = 0
base = results("arm64")
for a in sorted(base):
    same = all(partition(c, a) == partition("arm64", a) for c in CHIPS)
    fail += not same
    print("a%s partition (cells per rank) identical on all chips: %s  [%s]" % (a, same, "ok" if same else "FAIL"))
for chip in CHIPS:
    for a in sorted(base):
        it, gi, ex = work(chip, a)
        print("%-7s a%-3s work: %d SIMPLE iterations, %d GAMG iterations, %.0f s solver, %.3f ms per GAMG iteration"
              % (chip, a, it, gi, ex, 1000 * ex / gi))
for chip in CHIPS[1:]:
    other = results(chip)
    for a, r0 in sorted(base.items()):
        r1 = other[a]
        for q, dq in (("CL", "dCL_1000"), ("CD", "dCD_1000")):
            d = abs(float(r1[q]) - float(r0[q]))
            bound = 10 * max(float(r0[dq]), float(r1[dq]))
            ok = d <= bound
            fail += not ok
            print("%-7s a%-3s %s  %s vs %s  |d| %.2e  bound %.2e  [%s]"
                  % (chip, a, q, r1[q], r0[q], d, bound, "ok" if ok else "FAIL"))
        h0, h1 = history("arm64", a), history(chip, a)
        n = min(len(h0), len(h1))
        first = next((i for i in range(n) if h0[i]["Cl"] != h1[i]["Cl"] or h0[i]["Cd"] != h1[i]["Cd"]), None)
        mx = max(max(abs(h0[i]["Cl"] - h1[i]["Cl"]), abs(h0[i]["Cd"] - h1[i]["Cd"])) for i in range(n))
        p0, p1 = residuals("arm64", a), residuals(chip, a)
        pfirst = next((i + 1 for i in range(min(len(p0), len(p1))) if p0[i] != p1[i]), None)
        print("%-7s a%-3s path: coefficient history first differs at iteration %s, max |d| %.2e; p residual first differs at iteration %s"
              % (chip, a, None if first is None else int(h0[first]["Time"]), mx, pfirst))
# Intel against AMD: the same binary on two x86 chips
for a in sorted(base):
    hi, ha = history("x86_64", a), history("amd", a)
    same = len(hi) == len(ha) and all(x == y for x, y in zip(hi, ha))
    print("x86_64 vs amd a%s: coefficient history identical: %s" % (a, same))
sys.exit(1 if fail else 0)
