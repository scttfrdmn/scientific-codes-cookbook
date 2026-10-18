#!/usr/bin/env python3
"""Compare two chips' glmnet-real outputs: what is identical, and by how much the rest differs.

    python3 -I compare.py <reference out dir> <other out dir>

Reports; asserts nothing (the identities are asserted inside each run).
"""
import csv
import sys


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


a, b = sys.argv[1], sys.argv[2]
sa, sb = score(a), score(b)
skip = ("time_", "blas", "r_arch", "peak_rss", "A_peak_rss")
same = [k for k in sa if not k.startswith(skip) and sa.get(k) == sb.get(k)]
diff = [k for k in sa if not k.startswith(skip) and sa.get(k) != sb.get(k)]
print("score.tsv: %d observables identical" % len(same))
for k in diff:
    print("  differs  %-36s %s\n           %-36s %s" % (k, sa[k], "", sb.get(k)))

for leg in ("A", "B"):
    la, lb = rows("%s/%s-lambda.tsv" % (a, leg)), rows("%s/%s-lambda.tsv" % (b, leg))
    n = min(len(la), len(lb))
    rel = max(abs(float(x["lambda"]) - float(y["lambda"])) / float(x["lambda"]) for x, y in zip(la, lb))
    dfd = sum(x["df"] != y["df"] for x, y in zip(la[:n], lb[:n]))
    ddr = max(abs(float(x["dev_ratio"]) - float(y["dev_ratio"])) for x, y in zip(la[:n], lb[:n]))
    print("%s path: %d vs %d lambdas, lambda max rel diff %.3e, df differs at %d, dev_ratio max diff %.3e"
          % (leg, len(la), len(lb), rel, dfd, ddr))
    pa, pb = rows("%s/%s-path.tsv" % (a, leg)), rows("%s/%s-path.tsv" % (b, leg))
    ca = {(r["lambda_index"], r["gene"]): float(r["coef"]) for r in pa}
    cb = {(r["lambda_index"], r["gene"]): float(r["coef"]) for r in pb}
    supp = sum(1 for k in set(ca) ^ set(cb))
    lam_supp = len({k[0] for k in set(ca) ^ set(cb)})
    common = set(ca) & set(cb)
    mx = max((abs(ca[k] - cb[k]) for k in common), default=0.0)
    mr = max((abs(ca[k] - cb[k]) / abs(ca[k]) for k in common if ca[k] != 0), default=0.0)
    ident = sum(1 for k in common if ca[k] == cb[k])
    print("%s coefficients: %d nonzeros vs %d; support differs in %d entries over %d lambdas; "
          "%d of %d shared coefficients bit-identical; max |diff| %.3e, max rel %.3e"
          % (leg, len(ca), len(cb), supp, lam_supp, ident, len(common), mx, mr))
    va, vb = rows("%s/%s-cv.tsv" % (a, leg)), rows("%s/%s-cv.tsv" % (b, leg))
    n = min(len(va), len(vb))
    cr = max(abs(float(x["cvm"]) - float(y["cvm"])) / abs(float(x["cvm"])) for x, y in zip(va[:n], vb[:n]))
    print("%s cv curve: %d vs %d lambdas, cvm max rel diff %.3e" % (leg, len(va), len(vb), cr))
