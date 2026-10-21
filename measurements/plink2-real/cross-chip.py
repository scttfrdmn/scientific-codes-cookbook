#!/usr/bin/env python3
"""Cross-chip comparison of plink2-real's outputs, streamed from the bucket (nothing written to disk).

    COOKBOOK_BUCKET=<bucket> python3 cross-chip.py <prefixA> <prefixB>     e.g. runs runs-amd

Eigenvectors are compared after fixing each PC's sign (the eigenproblem's own ambiguity), and the
regression rows column by column, relative.
"""
import gzip
import hashlib
import os
import subprocess
import sys

B = "s3://%s/" % os.environ["COOKBOOK_BUCKET"]
A, X = sys.argv[1], sys.argv[2]


def get(key):
    return subprocess.run(["aws", "s3", "cp", "--only-show-errors", B + key, "-"],
                          check=True, capture_output=True).stdout


def sha(key):   # of the content: gzip bytes differ between the images' gzips, the table need not
    b = get(key)
    return hashlib.sha256(gzip.decompress(b) if key.endswith(".gz") else b).hexdigest()[:16]


def rel(a, b):
    a, b = float(a), float(b)
    return 0.0 if a == b else abs(a - b) / max(abs(a), abs(b))


print("== %s vs %s" % (A, X))
for f in ["p2/pca.eigenval", "p2/pcax.eigenval", "p2/pheno.txt", "p2/causal.txt", "p2/prune.prune.in",
          "p2/dup.txt", "p2/p2.counts.tsv.gz", "p19/pca19.eigenval", "p19/lin.assoc.linear"]:
    ka, kx = "%s/plink2-real/%s" % (A, f), "%s/plink2-real/%s" % (X, f)
    a, x = sha(ka), sha(kx)
    print("%-22s %s %s %s" % (f, a, x, "identical" if a == x else "DIFFER"))

va = [l.split() for l in get("%s/plink2-real/p2/pca.eigenvec" % A).decode().splitlines()[1:]]
vx = [l.split() for l in get("%s/plink2-real/p2/pca.eigenvec" % X).decode().splitlines()[1:]]
assert [r[0] for r in va] == [r[0] for r in vx]
ncol = len(va[0])
flipped, worst = [], 0.0
for j in range(ncol - 10, ncol):
    dot = sum(float(a[j]) * float(b[j]) for a, b in zip(va, vx))
    s = -1.0 if dot < 0 else 1.0
    if s < 0:
        flipped.append(j - (ncol - 10) + 1)
    worst = max(worst, max(abs(float(a[j]) - s * float(b[j])) for a, b in zip(va, vx)))
print("approx eigenvectors: PCs negated %s; max |loading diff| after sign %.2e" % (flipped or "none", worst))

ga = [l.split("\t") for l in get("%s/plink2-real/p2/glm.causal.tsv" % A).decode().splitlines()]
gx = [l.split("\t") for l in get("%s/plink2-real/p2/glm.causal.tsv" % X).decode().splitlines()]
h = ga[0]
for col in ("BETA", "SE", "T_STAT", "P"):
    i = h.index(col)
    print("glm %-7s max rel diff %.2e" % (col, max(rel(a[i], b[i]) for a, b in zip(ga[1:], gx[1:]))))
ra = get("%s/plink2-real/p2/recovery.tsv" % A)
rx = get("%s/plink2-real/p2/recovery.tsv" % X)
print("recovery.tsv %s" % ("identical" if ra == rx else "differs (it carries BETA, above)"))
