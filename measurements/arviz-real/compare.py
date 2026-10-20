#!/usr/bin/env python3
"""Compare two chips' arviz-real outputs.

    python3 -I compare.py <chip A dir> <chip B dir>

Fixed points, asserted: ArviZ's diagnostics of posteriordb's reference draws (the same staged
bytes on every chip), and ArviZ's and stansummary's diagnostics of every fresh fit whose draws are
byte-identical on both chips. Each is a deterministic function of identical doubles, so any
difference is rounding; FIXED_TOL = 1e-12 relative allows for a different SIMD path through numpy's
FFT and nothing else. Paths, reported: the draws themselves (NUTS amplifies a last-bit gradient
difference into a different trajectory), so a fit whose draws differ is compared only on what it
concludes.
"""
import csv
import gzip
import math
import os
import sys

FIXED_TOL = 1e-12


def rel(a, b):
    a, b = float(a), float(b)
    if math.isnan(a) and math.isnan(b):
        return 0.0
    if math.isnan(a) or math.isnan(b):
        return float("inf")
    if a == b:
        return 0.0
    return abs(a - b) / max(abs(a), abs(b))


def tsv(path):
    fh = open(path) if os.path.exists(path) else gzip.open(path + ".gz", "rt")
    return list(csv.DictReader(fh, delimiter="\t"))


def log(path):
    out = {}
    for line in open(path):
        k, _, v = line.rstrip("\n").partition("\t")
        out[k] = v
    return out


A, B = sys.argv[1], sys.argv[2]
fails = []

ra = {(r["posterior"], r["param"]): r for r in tsv(A + "/reference_diag.tsv")}
rb = {(r["posterior"], r["param"]): r for r in tsv(B + "/reference_diag.tsv")}
if set(ra) != set(rb):
    sys.exit("FAIL: the chips diagnosed different reference scalars")
cols = [c for c in next(iter(ra.values())) if c.startswith("az_")]
same = sum(1 for k in ra if all(ra[k][c] == rb[k][c] for c in cols))
worst = max(((rel(ra[k][c], rb[k][c]), k, c) for k in ra for c in cols), key=lambda t: t[0])
print("reference draws: %d scalars x %d ArviZ statistics; bit-identical in %d of %d scalars; worst rel %.3e (%s %s %s)"
      % (len(ra), len(cols), same, len(ra), worst[0], worst[1][0], worst[1][1], worst[2]))
if worst[0] > FIXED_TOL:
    fails.append("reference diagnostics differ across chips by %.3e" % worst[0])

da = {r["posterior"]: r["draws_sha256"] for r in tsv(A + "/draws.tsv")}
db = {r["posterior"]: r["draws_sha256"] for r in tsv(B + "/draws.tsv")}
ident = sorted(p for p in da if db.get(p) == da[p])
print("fresh fits: draws byte-identical in %d of %d posteriors" % (len(ident), len(da)))

fa = {(r["posterior"], r["param"]): r for r in tsv(A + "/fit_vs_stansummary.tsv")}
fb = {(r["posterior"], r["param"]): r for r in tsv(B + "/fit_vs_stansummary.tsv")}
vcols = [c for c in next(iter(fa.values())) if c.startswith("az_") or c.startswith("ss_")]
keys = [k for k in fa if k[0] in ident and k in fb]
n_same = sum(1 for k in keys if all(fa[k][c] == fb[k][c] for c in vcols))
if keys:
    w = max(((rel(fa[k][c], fb[k][c]), k, c) for k in keys for c in vcols), key=lambda t: t[0])
    print("  on those: %d scalars, ArviZ and stansummary values bit-identical in %d; worst rel %.3e (%s %s %s)"
          % (len(keys), n_same, w[0], w[1][0], w[1][1], w[2]))
    if w[0] > FIXED_TOL:
        fails.append("diagnostics of byte-identical draws differ across chips by %.3e" % w[0])
diff = [k for k in fa if k[0] not in ident and k in fb and fa[k]["degenerate"] == "False"]
if diff:
    w = max(((rel(fa[k]["az_r_hat"], fb[k]["az_r_hat"]), k) for k in diff), key=lambda t: t[0])
    print("  posteriors whose draws differ: %d scalars; ArviZ R-hat differs by up to %.3e rel (%s %s) -- a path, reported"
          % (len(diff), w[0], w[1][0], w[1][1]))

la, lb = log(A + "/arviz_real.log"), log(B + "/arviz_real.log")
for k in ("partF_flagged_rhat_gt_1.01_arviz", "partF_flagged_rhat_gt_1.01_stansummary"):
    print("%s: %s | %s" % (k, la.get(k), lb.get(k)))
for k in sorted(la):
    if k.startswith("partR_") and not k.endswith("_s"):
        if la[k] != lb.get(k):
            print("partR differs: %s: %s | %s" % (k, la[k], lb.get(k)))
if fails:
    for f in fails:
        print("FAIL: " + f)
    sys.exit(1)
print("OK")
