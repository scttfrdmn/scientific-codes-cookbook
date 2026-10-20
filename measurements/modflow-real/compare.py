#!/usr/bin/env python3
"""modflow-real: the three chips against each other and against the archive's own 6.3.0 run.

    python3 -u compare.py <dir> [<dir> ...]       # one directory per chip, Graviton4 first

Each directory holds one chip's staged outputs (real.task.json). Heads are compared on the 42,822
active cells, period by period. Exits 1 if an assertion fails; everything else is reported.
"""
import csv
import os
import re
import sys

import numpy as np
import flopy

DIRS = sys.argv[1:]
LABEL = {d: os.path.basename(os.path.normpath(d)) for d in DIRS}
CASES = ["postcal", "precal"]
DVCLOSE = 0.1   # the archive's own IMS OUTER_DVCLOSE, m: the head change at which mf6 calls a step converged
FAILS = []


def check(name, ok, detail):
    ok = bool(ok)
    print("%s\t%s\t%s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)


def results(d):
    out = {}
    for line in open(os.path.join(d, "results.tsv")):
        f = line.rstrip("\n").split("\t")
        if len(f) >= 3 and f[1] != "check":
            out[(f[0], f[1])] = f[2]
    return out


def heads(d, case):
    hf = flopy.utils.HeadFile(os.path.join(d, "%s-mobile_bay.hds" % case))
    return np.array([hf.get_data(kstpkper=k).reshape(-1) for k in hf.get_kstpkper()])


def obs(path):
    with open(path) as f:
        r = list(csv.reader(f))
    return r[0], np.array([[float(x) for x in row] for row in r[1:]])


R = {d: results(d) for d in DIRS}
for case in CASES:
    print("\n==== %s" % case)
    H = {d: heads(d, case) for d in DIRS}
    ref = H[DIRS[0]]
    active = np.abs(ref[0]) < 1e29
    nc = {d: R[d].get((case, "nonconverged_periods"), "?") for d in DIRS}
    for d in DIRS:
        print("%s\tnonconverged periods\t%s\t(archive 6.3.0: %s)" % (LABEL[d], nc[d], R[d].get((case, "nonconverged_periods_archive_6.3.0"))))

    # -- against the archive: the published heads, from mf6 6.3.0 on Windows/Intel Fortran
    for d in DIRS:
        rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(d, "vs-archive-%s.tsv" % case))][1:]
        conv = [r for r in rows if r[5] == "1" and r[6] == "1"]
        uncv = [r for r in rows if not (r[5] == "1" and r[6] == "1")]
        mx = max(float(r[1]) for r in conv)
        p99 = max(float(r[2]) for r in conv)
        med = max(float(r[3]) for r in conv)
        over = max(int(r[4]) for r in conv)
        print("%s\tvs archive, %d periods converged in both: worst max|dh| %.3e m, worst p99 %.3e m, worst median %.3e m, at most %d of %d cells over 0.1 m" % (
            LABEL[d], len(conv), mx, p99, med, over, active.sum()))
        if uncv:
            print("%s\tvs archive, %d periods non-converged in either: worst max|dh| %.3e m, worst p99 %.3e m" % (
                LABEL[d], len(uncv), max(float(r[1]) for r in uncv), max(float(r[2]) for r in uncv)))
        check("%s %s: 99%% of active cells within the solver's head closure (%.1f m) of the archive, every period converged in both" % (LABEL[d], case, DVCLOSE),
              p99 < DVCLOSE, "worst p99 %.3e m" % p99)
        for f in ["mobile_bay.gwf.head.obs.csv", "mobile_bay.gwf.sfr.obs.csv"]:
            h1, a = obs(os.path.join(d, "%s-%s" % (case, f)))
            h2, b = obs(os.path.join(d, "archive-%s-%s" % (case, f)))
            n = min(len(a), len(b))
            ok_hdr = h1 == h2 and np.array_equal(a[:n, 0], b[:n, 0])
            diff = np.abs(a[:n, 1:] - b[:n, 1:])
            rel = diff / np.maximum(np.abs(b[:n, 1:]), 1e-30)
            print("%s\t%s vs archive: %d obs x %d times, same names and times %s, max |diff| %.3e, median %.3e, max rel %.3e" % (
                LABEL[d], f, a.shape[1] - 1, n, ok_hdr, diff.max(), np.median(diff), rel[np.abs(b[:n, 1:]) > 1e-6].max(initial=0)))

    # -- across chips: every pair
    for i0, d0 in enumerate(DIRS):
        for d in DIRS[i0 + 1:]:
            A, B = H[d0], H[d]
            same = [np.array_equal(A[i], B[i]) for i in range(len(A))]
            dh = np.abs(A[:, active] - B[:, active])
            first = next((i + 1 for i, s in enumerate(same) if not s), None)
            print("%s vs %s\theads bit-identical in %d of %d periods; first difference at period %s; max |dh| %.3e m, worst p99 %.3e m, worst median %.3e m, at most %d cells over 0.1 m" % (
                LABEL[d0], LABEL[d], sum(same), len(same), first, dh.max(), np.percentile(dh, 99, axis=1).max(), np.median(dh, axis=1).max(),
                (dh > 0.1).sum(axis=1).max()))
            for f in ["mobile_bay.gwf.head.obs.csv", "mobile_bay.gwf.sfr.obs.csv"]:
                _, a = obs(os.path.join(d0, "%s-%s" % (case, f)))
                _, b = obs(os.path.join(d, "%s-%s" % (case, f)))
                print("%s vs %s\t%s: max |diff| %.3e" % (LABEL[d0], LABEL[d], f, np.abs(a - b).max()))
            ba = [l.split("\t") for l in open(os.path.join(d0, "budget-%s.tsv" % case))][1:]
            bb = [l.split("\t") for l in open(os.path.join(d, "budget-%s.tsv" % case))][1:]
            dp = max(abs(float(x[4]) - float(y[4])) for x, y in zip(ba, bb))
            print("%s vs %s\tGWF percent discrepancy: max per-period difference %.3e (percentage points)" % (LABEL[d0], LABEL[d], dp))
            # which periods converge is a property of the iteration path: reported, not asserted
            print("%s vs %s\tnon-converged periods: %s vs %s" % (LABEL[d0], LABEL[d], nc[d0], nc[d]))

if FAILS:
    print("\nFAILED:\n  " + "\n  ".join(FAILS))
    sys.exit(1)
print("\nCOMPARE OK")
