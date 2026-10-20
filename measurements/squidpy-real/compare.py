"""Compare two chips' squidpy-real outputs (each the r1/ directory a run staged out).

    python3 -I compare.py <chip A r1/> <chip B r1/>

Reports, never asserts: every assertion was already made inside each run.
"""
import os
import sys

import numpy as np
import pandas as pd

A, B = sys.argv[1], sys.argv[2]


def same_bytes(f):
    pa, pb = os.path.join(A, f), os.path.join(B, f)
    return open(pa, "rb").read() == open(pb, "rb").read()


for s in ("slideseq", "visium"):
    print(f"-- {s}")
    d = os.path.join(A, s)
    for f in sorted(os.listdir(d)):
        if f.endswith("_edges.npy"):
            print(f"  {f[:-4]}: {'identical' if same_bytes(os.path.join(s, f)) else 'DIFFER'}")
    ca = pd.read_csv(os.path.join(A, s, "nhood_count.tsv"), sep="\t", index_col=0).to_numpy()
    cb = pd.read_csv(os.path.join(B, s, "nhood_count.tsv"), sep="\t", index_col=0).to_numpy()
    print(f"  enrichment counts: {'identical' if np.array_equal(ca, cb) else 'DIFFER'}")
    za = pd.read_csv(os.path.join(A, s, "nhood_z.tsv"), sep="\t", index_col=0).to_numpy()
    zb = pd.read_csv(os.path.join(B, s, "nhood_z.tsv"), sep="\t", index_col=0).to_numpy()
    print(f"  enrichment z (seeded, 1000 perms): {'identical' if np.array_equal(za, zb, equal_nan=True) else 'max |dz| %.3e' % np.nanmax(np.abs(za - zb))}")
    if os.path.exists(os.path.join(A, s, "co_occurrence.npy")):
        oa, ob = np.load(os.path.join(A, s, "co_occurrence.npy")), np.load(os.path.join(B, s, "co_occurrence.npy"))
        print(f"  co-occurrence: {'identical' if np.array_equal(oa, ob) else 'max |diff| %.3e' % np.abs(oa - ob).max()}")
    for f, col in (("moran.tsv", "I"), ("geary.tsv", "C"), ("moran_perm.tsv", "I")):
        ta = pd.read_csv(os.path.join(A, s, f), sep="\t", index_col=0)
        tb = pd.read_csv(os.path.join(B, s, f), sep="\t", index_col=0).loc[ta.index]
        x, y = ta[col].to_numpy(), tb[col].to_numpy()
        ne = int((x != y).sum())
        line = f"  {f} {col}: {len(x) - ne} of {len(x)} bit-identical, max |diff| {np.abs(x - y).max():.3e}"
        if "pval_sim" in ta:
            p, q = ta["pval_sim"].to_numpy(), tb["pval_sim"].to_numpy()
            line += f"; pval_sim identical on {int((p == q).sum())} of {len(p)}"
            v, w = ta["var_sim"].to_numpy(), tb["var_sim"].to_numpy()
            line += f"; var_sim max rel {np.nanmax(np.abs(v - w) / np.abs(v)):.1e}"
        rank = (ta[col].rank(method="first").to_numpy() == tb[col].rank(method="first").to_numpy()).all()
        print(line + f"; gene ranking {'identical' if rank else 'differs'}")
    sa = pd.read_csv(os.path.join(A, s, "score.tsv"), sep="\t")
    sb = pd.read_csv(os.path.join(B, s, "score.tsv"), sep="\t")
    m = sa.merge(sb, on="check", how="outer", suffixes=("_a", "_b"))
    diff = m[m["value_a"] != m["value_b"]]
    print(f"  score.tsv: {len(m) - len(diff)} of {len(m)} rows print identically; differing:")
    for _, r in diff.iterrows():
        print(f"    {str(r['check'])[:90]}: {str(r['value_a'])[:120]}  |  {str(r['value_b'])[:120]}")
