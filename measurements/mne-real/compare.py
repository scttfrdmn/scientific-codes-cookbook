#!/usr/bin/env python3
"""Compare two chips' mne-real outputs: which score rows are identical, and how far the arrays moved.

    python3 -I compare.py <dir A> <dir B>
"""
import os
import sys

import numpy as np

A, B = sys.argv[1], sys.argv[2]


def rows(d):
    out = {}
    for line in open(os.path.join(d, "score.tsv")):
        k, _, v = line.rstrip("\n").partition("\t")
        out[k] = v
    return out


ra, rb = rows(A), rows(B)
skip = {"threadpools", "python_wall_s"}
same = [k for k in ra if k not in skip and ra[k] == rb.get(k)]
diff = [k for k in ra if k not in skip and ra[k] != rb.get(k)]
print("score rows identical: %d of %d" % (len(same), len(same) + len(diff)))
for k in diff:
    print("  differs  %-26s A: %s" % (k, ra[k]))
    print("  %-34s B: %s" % ("", rb.get(k)))


def rel(x, y):
    return float(np.max(np.abs(x - y)) / np.max(np.abs(x)))


ea, eb = np.load(os.path.join(A, "evoked.npz")), np.load(os.path.join(B, "evoked.npz"))
for k in ("Famous", "Unfamiliar", "Scrambled"):
    print("evoked %-10s max|diff|/max %.2e  bit-identical %s" % (k, rel(ea[k], eb[k]), np.array_equal(ea[k], eb[k])))
sa, sb = np.load(os.path.join(A, "epoch-selection.npy")), np.load(os.path.join(B, "epoch-selection.npy"))
print("epoch selection identical: %s (%d kept)" % (np.array_equal(sa, sb), len(sa)))
ma, mb = np.load(os.path.join(A, "ica-mixing.npy")), np.load(os.path.join(B, "ica-mixing.npy"))
if ma.shape == mb.shape:
    print("ica mixing %s: max|diff|/max %.2e" % (ma.shape, rel(ma, mb)))
else:
    print("ica mixing shapes differ: %s vs %s" % (ma.shape, mb.shape))
sva, svb = np.load(os.path.join(A, "sss-sv-run01.npy")), np.load(os.path.join(B, "sss-sv-run01.npy"))
n = 44
print("SSS run-01 singular values, first %d: max rel diff %.2e" % (n, float(np.max(np.abs(sva[:n] - svb[:n]) / sva[:n]))))
da, db = np.load(os.path.join(A, "decoding.npz")), np.load(os.path.join(B, "decoding.npz"))
print("decoding AUC curve: max|diff| %.2e; nulls max|diff| %.2e"
      % (float(np.max(np.abs(da["auc"] - db["auc"]))), float(np.max(np.abs(da["null"] - db["null"])))))
pa, pb = np.load(os.path.join(A, "dipole.npz")), np.load(os.path.join(B, "dipole.npz"))
print("real dipole fits (12 times): max position diff %.2e mm, max GOF diff %.2e %%"
      % (1e3 * float(np.max(np.linalg.norm(pa["pos"] - pb["pos"], axis=1))), float(np.max(np.abs(pa["gof"] - pb["gof"])))))
print("planted-dipole fit: position diff %.2e mm" % (1e3 * float(np.linalg.norm(pa["planted"][:3] - pb["planted"][:3]))))
