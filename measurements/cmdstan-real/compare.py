#!/usr/bin/env python3
"""Compare two chips' cmdstan-real outputs: the log density at fixed points, then the paths.

    python3 -I compare.py <chip A dir> <chip B dir>

The log density and gradient at a reference draw are a deterministic evaluation, so they are the
fixed point here; the draws and the means are a path (NUTS amplifies the last bit), so they are
reported, never asserted.
"""
import sys


def rows(path):
    return [l.rstrip("\n").split("\t") for l in open(path) if l.strip()]


A, B = sys.argv[1], sys.argv[2]

la = {(r[0], r[1]): r[2:] for r in rows(A + "/lp.tsv")}
lb = {(r[0], r[1]): r[2:] for r in rows(B + "/lp.tsv")}
if set(la) != set(lb):
    sys.exit("FAIL: the two chips evaluated different log-density points")
same = worst_lp = worst_g = 0.0
nerr, worst_at, worst_g_at = 0, "", ""
for k in sorted(la):
    a, b = la[k], lb[k]
    if a[0].startswith("ERROR") or b[0].startswith("ERROR"):
        nerr += 1
        continue
    if a == b:
        same += 1
    fa, fb = [float(x) for x in a], [float(x) for x in b]
    d = abs(fa[0] - fb[0]) / max(abs(fa[0]), 1e-300)
    if d > worst_lp:
        worst_lp, worst_at = d, "%s draw %s" % k
    # relative to the gradient's own scale at that point, so a component that happens to be near
    # zero (cancellation) does not read as a large relative error
    gscale = max([abs(x) for x in fa[1:]] + [1e-300])
    for x, y in zip(fa[1:], fb[1:]):
        if abs(x - y) / gscale > worst_g:
            worst_g, worst_g_at = abs(x - y) / gscale, "%s draw %s" % k
n = len(la) - nerr
print("log density + gradient at %d reference draws (%d errored on either chip)" % (n, nerr))
print("  bit-identical (lp__ and every gradient component): %d of %d" % (same, n))
print("  max relative |lp__ difference|:   %.3e  (%s)" % (worst_lp, worst_at))
print("  max gradient difference / max|gradient|: %.3e  (%s)" % (worst_g, worst_g_at))

# A deterministic double-precision evaluation, so the bound is rounding, not a band: lp__ is a sum
# of at most a few thousand terms (~1e4 eps relative); the gradient is reverse-mode AD through the
# same terms. Both bounds sit far above any rounding difference and far below any wrong density.
LP_TOL, G_TOL = 1e-12, 1e-10
ok = worst_lp <= LP_TOL and worst_g <= G_TOL
print("  FIXED POINT: lp__ <= %g and gradient <= %g relative: %s" % (LP_TOL, G_TOL, "holds" if ok else "FAILS"))

pa = {r[0]: r for r in rows(A + "/posteriors.tsv")}
pb = {r[0]: r for r in rows(B + "/posteriors.tsv")}
hdr = pa.pop("posterior")
pb.pop("posterior")
i = hdr.index("draws_sha256")
ident = sorted(p for p in pa if pa[p][i] == pb[p][i])
print("posteriors whose 4000 draws are byte-identical across the two chips: %d of %d" % (len(ident), len(pa)))
if ident and len(ident) < len(pa):
    print("  identical: " + " ".join(ident))

ma = {(r[0], r[1]): r for r in rows(A + "/params.tsv")}
mb = {(r[0], r[1]): r for r in rows(B + "/params.tsv")}
h = ma.pop(("posterior", "param"))
mb.pop(("posterior", "param"))
im, isd = h.index("mean"), h.index("ref_sd")
dm = sorted((abs(float(ma[k][im]) - float(mb[k][im])) / float(ma[k][isd]), k) for k in ma)
print("posterior means: identical in %d of %d parameters; max |difference| %.3f reference sd (%s %s)"
      % (sum(1 for k in ma if ma[k][im] == mb[k][im]), len(ma), dm[-1][0], dm[-1][1][0], dm[-1][1][1]))
if not ok:
    sys.exit("FAIL: the log density at a fixed point differs across chips beyond rounding")
