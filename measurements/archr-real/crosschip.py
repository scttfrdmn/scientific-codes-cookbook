#!/usr/bin/env python3
"""Compare archr-real outputs across chips (each a directory of one run's r1/ outputs).

    python3 -I crosschip.py arm64/ x86_64/ amd/

The first directory is the reference. Counts and QC are fixed points, so they are compared by
md5. Iterative LSI and the clusters built on it are paths: reported as max |diff| per LSI
dimension after fixing each dimension's sign, and as the adjusted Rand index between partitions.
"""
import gzip
import hashlib
import re
import sys
from collections import Counter


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def logval(d, key):
    for line in open(d + "archr.log"):
        if line.startswith(key + "\t"):
            return line.rstrip("\n").split("\t", 1)[1]
    return "?"


def clusters(d):
    out = {}
    with open(d + "clusters.tsv") as f:
        next(f)
        for line in f:
            b, c, _ = line.rstrip("\n").split("\t")
            out[b] = c
    return out


def lsi(d):
    out = {}
    with gzip.open(d + "lsi.tsv.gz", "rt") as f:
        next(f)
        for line in f:
            x = line.rstrip("\n").split("\t")
            out[x[0]] = [float(v) for v in x[1:]]
    return out


def ari(a, b):
    keys = sorted(a)
    c2 = lambda n: n * (n - 1) / 2
    t = Counter((a[k], b[k]) for k in keys)
    ra, rb = Counter(a[k] for k in keys), Counter(b[k] for k in keys)
    s = sum(c2(v) for v in t.values())
    ea, eb = sum(c2(v) for v in ra.values()), sum(c2(v) for v in rb.values())
    e = ea * eb / c2(len(keys))
    return (s - e) / ((ea + eb) / 2 - e)


dirs = sys.argv[1:]
ref = dirs[0]
rc, rl = clusters(ref), lsi(ref)
for d in dirs:
    core = "?"
    for line in open(d + "archr.log"):
        if line.startswith("Core:"):
            core = line.split()[1]
    print("== %s  core %s" % (d, core))
    print("  cell_qc.tsv md5      %s" % md5(d + "cell_qc.tsv"))
    print("  gene_scores md5      %s" % re.sub(r".*md5\s+", "", logval(d, "gene_scores")))
    print("  checks               %s" % [l.strip() for l in open(d + "archr.log") if l.startswith("checks:")])
    print("  clusters             %s" % logval(d, "clusters").split("sizes")[0].strip())
    print("  vs_signac            %s" % logval(d, "vs_signac"))
    if d == ref:
        continue
    c, l = clusters(d), lsi(d)
    print("  ARI vs %s       %.6f" % (ref, ari(rc, c)))
    cells = sorted(rl)
    nd = len(rl[cells[0]])
    worst = []
    for k in range(nd):
        dot = sum(rl[b][k] * l[b][k] for b in cells)
        sgn = 1.0 if dot >= 0 else -1.0
        worst.append(max(abs(rl[b][k] - sgn * l[b][k]) for b in cells))
    scale = max(abs(rl[b][k]) for b in cells for k in range(nd))
    print("  LSI max|diff| (sign-fixed) dims 1-5 %s ; all dims %.3g ; max|value| %.3g" %
          (", ".join("%.3g" % w for w in worst[:5]), max(worst), scale))
    print("  markers_canonical identical: %s" % (md5(d + "markers_canonical.tsv") == md5(ref + "markers_canonical.tsv")))
