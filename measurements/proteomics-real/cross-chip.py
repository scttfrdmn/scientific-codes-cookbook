#!/usr/bin/env python3
"""Cross-chip comparison of proteomics-real outputs, streamed from S3 (nothing written to disk).
   python3 cross-chip.py <prefixA> <prefixB>   e.g. runs runs-amd > results/cross-chip-amd.txt"""
import subprocess, sys
B = "s3://%s/" % __import__("os").environ["COOKBOOK_BUCKET"]
RUNS = "GFP_C001_1 GFP_C001_2 GFP_C001_3 MCL1_c007_1 MCL1_c007_2 MCL1_c007_3".split()
A, X = sys.argv[1], sys.argv[2]


def get(key):
    return subprocess.run(["aws", "s3", "cp", "--only-show-errors", B + key, "-"], check=True,
                          capture_output=True).stdout.decode().splitlines()


def rel(a, b):
    a, b = float(a), float(b)
    return 0.0 if a == b else abs(a - b) / max(abs(a), abs(b))


print("== search (%s vs %s)" % (A, X))
for r in RUNS:
    pa = [l.split("\t") for l in get("%s/proteomics-real/r1/search/psm_%s.tsv" % (A, r))]
    px = [l.split("\t") for l in get("%s/proteomics-real/r1/search/psm_%s.tsv" % (X, r))]
    assert len(pa) == len(px)
    sd = sum(1 for a, b in zip(pa, px) if a[2:] != b[2:])
    qd = sum(1 for a, b in zip(pa, px) if a[1] != b[1])
    rs = [rel(a[0], b[0]) for a, b in zip(pa, px) if a[0] != b[0]]
    na = get("%s/proteomics-real/r1/search/pin_%s.tsv" % (A, r))
    nx = get("%s/proteomics-real/r1/search/pin_%s.tsv" % (X, r))
    hdr = na[0].split("\t")
    cols = {}
    for la, lx in zip(na[1:], nx[1:]):
        fa, fx = la.split("\t"), lx.split("\t")
        for i, (u, v) in enumerate(zip(fa, fx)):
            if u != v:
                cols.setdefault(hdr[i], []).append(rel(u, v) if i not in (0, 20, 21) else 1.0)
    print("%s psm rows %d | seq/decoy differ %d | q differ %d | raw score differ %d max rel %.3e | pin rows %d %d, columns differing: %s"
          % (r, len(pa), sd, qd, len(rs), max(rs or [0]), len(na), len(nx),
             ", ".join("%s %d (max rel %.3e)" % (k, len(v), max(v)) for k, v in cols.items()) or "none"))

print("== percolator target tables, seed 1 (%s vs %s)" % (A, X))
for r in RUNS:
    ta = [l.split("\t") for l in get("%s/proteomics-real/r1/rescore/t_%s_a.tab" % (A, r))][1:]
    tx = [l.split("\t") for l in get("%s/proteomics-real/r1/rescore/t_%s_a.tab" % (X, r))][1:]
    order = [a[0] for a in ta] == [b[0] for b in tx]
    out = []
    for i, name in ((1, "score"), (2, "q-value"), (3, "PEP"), (4, "peptide")):
        d = [(a[i], b[i]) for a, b in zip(ta, tx) if a[i] != b[i]]
        m = max([abs(float(u) - float(v)) for u, v in d] or [0]) if i < 4 else 0
        out.append("%s %d (max abs %.3e)" % (name, len(d), m))
    acc_a = {a[0] for a in ta if float(a[2]) <= 0.01}
    acc_x = {b[0] for b in tx if float(b[2]) <= 0.01}
    print("%s rows %d %d same order %s | %s | accepted %d %d identical %s" % (r, len(ta), len(tx), order, " | ".join(out), len(acc_a), len(acc_x), acc_a == acc_x))
