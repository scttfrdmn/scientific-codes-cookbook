#!/usr/bin/env python3
"""Compare the per-entry tables of the three chips, column by column.

    python3 -I compare.py arm64/entries.tsv.gz x86_64/entries.tsv.gz amd/entries.tsv.gz

Every column is either text gemmi read (ids, counts, names, recorded values) or a number gemmi
computed (d_min from Miller indices and the cell, a polymer weight). The first kind must be
byte-identical across chips; the second may move in the last bits, and this says where.
"""
import gzip
import sys


def load(p):
    with gzip.open(p, "rt") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        return hdr, {l.split("\t", 1)[0]: l.rstrip("\n").split("\t") for l in f}


names = ["arm64", "x86_64", "amd"]
tabs = [load(p) for p in sys.argv[1:4]]
hdr = tabs[0][0]
assert all(t[0] == hdr for t in tabs), "headers differ"
assert all(set(t[1]) == set(tabs[0][1]) for t in tabs), "entry sets differ"
ids = sorted(tabs[0][1])
print("entries\t%d" % len(ids))
for a, b in [(0, 1), (0, 2), (1, 2)]:
    A, B = tabs[a][1], tabs[b][1]
    diff = {}
    ex = {}
    for i in ids:
        if A[i] == B[i]:
            continue
        for k, (u, v) in enumerate(zip(A[i], B[i])):
            if u != v:
                diff[hdr[k]] = diff.get(hdr[k], 0) + 1
                ex.setdefault(hdr[k], (i, u, v))
    print("%s vs %s\tentries differing\t%d" % (names[a], names[b], sum(1 for i in ids if A[i] != B[i])))
    for k in sorted(diff):
        i, u, v = ex[k]
        print("  %s\t%d entries\te.g. %s: %s | %s" % (k, diff[k], i, u, v))
