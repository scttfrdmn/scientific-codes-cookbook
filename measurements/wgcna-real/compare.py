#!/usr/bin/env python3
"""Compare two chips' wgcna-real outputs: what is identical, and by how much the rest differs.

    python3 -I compare.py <reference out dir> <other out dir>

Reports; asserts nothing (the identities are asserted inside each run).
"""
import csv
import sys
from collections import Counter


def rows(path):
    with open(path) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def score(d):
    out = {}
    with open(d + "/score.tsv") as f:
        next(f)
        for line in f:
            k, _, v = line.rstrip("\n").partition("\t")
            out[k] = v
    return out


def ari(a, b):
    c2 = lambda x: x * (x - 1) / 2
    n = len(a)
    s = sum(c2(v) for v in Counter(zip(a, b)).values())
    sa = sum(c2(v) for v in Counter(a).values())
    sb = sum(c2(v) for v in Counter(b).values())
    e = sa * sb / c2(n)
    den = (sa + sb) / 2 - e
    if den == 0:   # both partitions are one cluster (the all-grey control): undefined, reported as such
        return float("nan")
    return (s - e) / den


a, b = sys.argv[1], sys.argv[2]
sa, sb = score(a), score(b)
skip = ("time_", "blas", "r_arch", "arch", "peak_rss", "versions")
keys = [k for k in sa if not k.startswith(skip)]
same = [k for k in keys if sa.get(k) == sb.get(k)]
print("score.tsv: %d of %d observables identical" % (len(same), len(keys)))
for k in keys:
    if sa.get(k) != sb.get(k):
        print("  differs  %-34s %s\n           %-34s %s" % (k, sa[k], "", sb.get(k)))

ma, mb = rows(a + "/modules.tsv"), rows(b + "/modules.tsv")
assert [r["gene"] for r in ma] == [r["gene"] for r in mb], "gene lists differ"
for col in ("module", "blocked_5000", "control"):
    x, y = [r[col] for r in ma], [r[col] for r in mb]
    nd = sum(p != q for p, q in zip(x, y))
    print("modules.tsv %-13s %d of %d genes assigned differently; ARI %.6f" % (col, nd, len(x), ari(x, y)))
kw = [(float(p["kWithin"]), float(q["kWithin"])) for p, q in zip(ma, mb)]
print("kWithin: %d of %d bit-identical; max rel diff %.3e" % (
    sum(p == q for p, q in kw), len(kw), max(abs(p - q) / max(abs(p), 1e-300) for p, q in kw)))

ea, eb = rows(a + "/eigengenes.tsv"), rows(b + "/eigengenes.tsv")
cols = [c for c in ea[0] if c != "sample"]
if cols != [c for c in eb[0] if c != "sample"]:
    print("eigengenes: module labels differ")
else:
    worst = 0.0
    for c in cols:
        x = [float(r[c]) for r in ea]; y = [float(r[c]) for r in eb]
        sgn = 1.0 if sum(p * q for p, q in zip(x, y)) >= 0 else -1.0
        worst = max(worst, max(abs(p - sgn * q) for p, q in zip(x, y)))
    print("eigengenes: %d MEs x %d samples; max|diff| %.3e" % (len(cols), len(ea), worst))
