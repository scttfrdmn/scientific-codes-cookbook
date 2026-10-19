#!/usr/bin/env python3
"""Compare two chips' HUMAnN tables row by row.

    python3 compare.py <dir A> <dir B>

For each of genefamilies / pathabundance / pathcoverage: rows only in A, only in B, rows whose value
differs, and the largest relative difference among shared rows (community and stratified alike).
"""
import glob
import os
import sys


def load(d, t):
    p = glob.glob(os.path.join(d, "*_%s.tsv" % t))[0]
    return dict(l.rstrip("\n").split("\t") for l in open(p) if not l.startswith("#"))


a, b = sys.argv[1], sys.argv[2]
for t in ("genefamilies", "pathabundance", "pathcoverage"):
    A, B = load(a, t), load(b, t)
    shared = A.keys() & B.keys()
    diff = [k for k in shared if A[k] != B[k]]
    rel = 0.0
    for k in diff:
        x, y = float(A[k]), float(B[k])
        rel = max(rel, abs(x - y) / max(abs(x), abs(y)))
    comm = [k for k in diff if "|" not in k]
    print("%-13s rows %d / %d  only_A %d  only_B %d  differ %d (community %d)  max_rel %.2e  UNMAPPED %s / %s"
          % (t, len(A), len(B), len(A.keys() - B.keys()), len(B.keys() - A.keys()), len(diff), len(comm), rel,
             A.get("UNMAPPED"), B.get("UNMAPPED")))

# The two search tiers. A species stratum comes from the nucleotide search (bowtie2 against the
# profile's pangenomes); an `unclassified` stratum from the translated search (diamond against UniRef90).
GA, GB = load(a, "genefamilies"), load(b, "genefamilies")
for name, sel in (("nucleotide tier (species strata)", lambda k: "|" in k and not k.endswith("|unclassified")),
                  ("translated tier (unclassified)", lambda k: k.endswith("|unclassified"))):
    ka, kb = {k for k in GA if sel(k)}, {k for k in GB if sel(k)}
    d = [k for k in ka & kb if GA[k] != GB[k]]
    print("%-33s rows %d / %d  only_A %d  only_B %d  differ %d  RPK sum %.4f / %.4f"
          % (name, len(ka), len(kb), len(ka - kb), len(kb - ka), len(d),
             sum(float(GA[k]) for k in ka), sum(float(GB[k]) for k in kb)))
