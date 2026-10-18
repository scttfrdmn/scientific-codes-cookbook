#!/usr/bin/env python3
"""Compare the three chips' Signac runs: python3 -I crosschip.py <arm64 dir> <x86_64 dir> <amd dir>

Each dir holds that run's signac.log, qc.tsv, singular_values.txt and lsi_u.tsv.gz (aws s3 cp
--recursive from runs/, runs-x86_64/, runs-amd/ signac-real/r1/). The counts and the QC table are
fixed points (integers and ratios of integers), so their md5s must match exactly; the singular
values and LSI embedding come out of a Lanczos solve on BLAS, so they are reported as differences.
"""
import gzip, sys

def md5s(d):
    for line in open(d + "/signac.log"):
        if line.startswith("md5\t"):
            f = line.split("\t", 1)[1].split()
            return dict(zip(f[0::2], f[1::2]))
    sys.exit("no md5 line in %s/signac.log" % d)

def sv(d):
    return [float(x) for x in open(d + "/singular_values.txt")]

def lsi(d):
    with gzip.open(d + "/lsi_u.tsv.gz", "rt") as f:
        next(f)
        return {r[0]: [float(x) for x in r[1:]] for r in (l.rstrip("\n").split("\t") for l in f)}

dirs = dict(zip(("arm64", "x86_64", "amd"), sys.argv[1:4]))
base = "arm64"
m = {a: md5s(d) for a, d in dirs.items()}
ok = True
for a in dirs:
    if a == base:
        continue
    for k, v in m[base].items():
        if k == "singular_values":
            continue
        same = m[a].get(k) == v
        ok &= same
        print("%-7s vs arm64  %-16s %s" % (a, k, "identical" if same else "DIFFERENT"))
    s0, s1 = sv(dirs[base]), sv(dirs[a])
    rel = max(abs(x - y) / x for x, y in zip(s0, s1))
    print("%-7s vs arm64  singular values  max rel diff %.2e (all 50)" % (a, rel))
    u0, u1 = lsi(dirs[base]), lsi(dirs[a])
    cells = sorted(u0)
    for k in (0, 1, 9, 29, 49):
        d = max(abs(u0[c][k] - u1[c][k]) for c in cells)
        print("%-7s vs arm64  LSI_%-2d u        max abs diff %.2e (unit-norm columns, sign fixed)" % (a, k + 1, d))
if not ok:
    sys.exit("FAIL: a fixed-point output differs between chips")
print("CROSSCHIP OK: every count and QC value is byte-identical on the three chips")
