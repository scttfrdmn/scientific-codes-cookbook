#!/usr/bin/env python3
"""Compare two scvelo-real result directories (two chips, or the sizing probe against a full run).

    python3 -I compare.py <r1 dir A> <r1 dir B>

numpy only. Reports which observables are bit-identical (fixed points) and how far the rest moved
(paths). The one assertion: on genes both runs fitted from identical moments, a per-gene fit that
is a fixed point must match exactly -- so it is reported, not assumed.
"""
import csv
import os
import sys

import numpy as np

A, B = sys.argv[1], sys.argv[2]


def score(d):
    with open(os.path.join(d, "score.tsv")) as fh:
        return {r["observable"]: r["value"] for r in csv.DictReader(fh, delimiter="\t")}


def fitvar(d):
    with open(os.path.join(d, "fit_var.tsv")) as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    key = "index" if "index" in rows[0] else list(rows[0].keys())[0]
    return {r[key]: r for r in rows}


def rank(x):
    o = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[o] = np.arange(len(x))
    xs = x[o]
    i = 0
    while i < len(xs):          # average ties
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        r[o[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(x, y):
    return float(np.corrcoef(rank(np.asarray(x)), rank(np.asarray(y)))[0, 1])


sa, sb = score(A), score(B)
skip = ("wall_s", "peak_rss_gib", "probe_", "n_jobs")
same, diff = [], []
for k in sa:
    if k in sb and not k.startswith(skip):
        (same if sa[k] == sb[k] else diff).append(k)
print("score rows identical: %d -- %s" % (len(same), ", ".join(same)))
for k in diff:
    print("differs  %-30s A: %s" % (k, sa[k]))
    print("         %-30s B: %s" % ("", sb[k]))

ha = open(os.path.join(A, "hvg.txt")).read()
hb = open(os.path.join(B, "hvg.txt")).read()
print("hvg list (members and order): %s" % ("identical" if ha == hb else "DIFFERS"))

fa, fb = fitvar(A), fitvar(B)
genes = [g for g in fa if g in fb]
print("genes sent to recover_dynamics (the steady-state velocity genes): %d vs %d, shared %d"
      % (len(fa), len(fb), len(genes)))
ss = np.array([[float(fa[g]["velocity_gamma"]), float(fb[g]["velocity_gamma"])] for g in genes])
print("steady-state gamma on %d genes: %d bit-identical, max rel diff %.2e"
      % (len(genes), int((ss[:, 0] == ss[:, 1]).sum()), np.max(np.abs(ss[:, 0] - ss[:, 1]) / np.abs(ss[:, 1]))))
for c in ("stoch_genes",):
    a = {g for g in genes if fa[g].get(c) == "True"}
    b = {g for g in genes if fb[g].get(c) == "True"}
    print("%s: %d vs %d, shared %d" % (c, len(a), len(b), len(a & b)))

cols = ["fit_alpha", "fit_beta", "fit_gamma", "fit_t_", "fit_scaling", "fit_likelihood"]
both = [g for g in genes if fa[g]["fit_alpha"] not in ("", "nan") and fb[g]["fit_alpha"] not in ("", "nan")]
if both:
    P = np.array([[float(fa[g][c]) for c in cols] for g in both])
    Q = np.array([[float(fb[g][c]) for c in cols] for g in both])
    ident = np.all(P == Q, axis=1)
    print("dynamical fit on %d genes fitted by both: %d bit-identical in all of %s"
          % (len(both), int(ident.sum()), ",".join(cols)))
    for j, c in enumerate(cols):
        rel = np.abs(P[:, j] - Q[:, j]) / np.maximum(np.abs(Q[:, j]), 1e-300)
        print("  %-15s identical %4d  median rel %.2e  max rel %.2e  Spearman %.6f"
              % (c, int((P[:, j] == Q[:, j]).sum()), np.median(rel), rel.max(), spearman(P[:, j], Q[:, j])))
    la, lb = P[:, 5] > 1e-3, Q[:, 5] > 1e-3
    print("  likelihood > 0.001 (scVelo's dynamical-genes cut): %d vs %d, both %d"
          % (int(la.sum()), int(lb.sum()), int((la & lb).sum())))
    g2b = P[:, 2] / P[:, 1], Q[:, 2] / Q[:, 1]
    print("  gamma/beta Spearman %.6f" % spearman(*g2b))

pa, pb = os.path.join(A, "pseudotime.npy"), os.path.join(B, "pseudotime.npy")
if os.path.exists(pa) and os.path.exists(pb):
    x, y = np.load(pa), np.load(pb)
    print("velocity pseudotime: %s, max |diff| %.3e, Spearman %.6f"
          % ("identical" if np.array_equal(x, y) else "differs", np.max(np.abs(x - y)), spearman(x, y)))
