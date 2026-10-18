#!/usr/bin/env python3
"""Compare two iqtree-real runs (two chips, or two runs on one) from their staged outputs.

    python3 compare.py <runA>/iqtree <runB>/iqtree
    python3 compare.py --models <A>.model.gz <B>.model.gz      # ModelFinder fits only

Prints what moved: ModelFinder's per-model log-likelihoods on its starting tree, split into the
gamma/invariant fits and the FreeRate (+R) fits; the Robinson-Foulds distance between the two ML
trees; UFBoot support differences on the splits they share; final log-likelihoods; iterations.
"""
import gzip
import math
import os
import re
import sys


def newick_splits(text):
    """Bipartitions of an unrooted Newick tree, each as the frozenset on the side without taxon 0."""
    text = re.sub(r":[0-9.eE+-]+", "", text.strip().rstrip(";"))
    stack, splits, support = [[]], [], {}
    i = 0
    while i < len(text):
        c = text[i]
        if c == "(":
            stack.append([])
            i += 1
        elif c in ",":
            i += 1
        elif c == ")":
            clade = stack.pop()
            i += 1
            m = re.match(r"[0-9.]+", text[i:])
            lab = None
            if m:
                lab = float(m.group(0))
                i += len(m.group(0))
            leaves = frozenset(x for sub in clade for x in (sub if isinstance(sub, frozenset) else [sub]))
            splits.append((leaves, lab))
            stack[-1].append(leaves)
        else:
            m = re.match(r"[^(),;]+", text[i:])
            stack[-1].append(m.group(0))
            i += len(m.group(0))
    taxa = frozenset(x for sub in stack[0] for x in (sub if isinstance(sub, frozenset) else [sub]))
    ref = sorted(taxa)[0]
    out = {}
    for leaves, lab in splits:
        side = leaves if ref not in leaves else taxa - leaves
        if 1 < len(side) < len(taxa) - 1:
            out[side] = lab
    return taxa, out


def iqtree_logl(path):
    for line in open(path):
        if line.startswith("Log-likelihood of the tree:"):
            return float(line.split()[4])


def models(path):
    out = {}
    with gzip.open(path, "rt") as f:
        for line in f:
            p = line.split()
            if len(p) == 4 and p[0].endswith(":") and re.match(r"-[0-9.]+$", p[1]) and p[2].isdigit():
                out[p[0][:-1]] = (float(p[1]), int(p[2]))
    return out


def compare_models(ma, mb):
    common = sorted(set(ma) & set(mb))
    ddf = sum(ma[m][1] != mb[m][1] for m in common)
    print("modelfinder_models", len(ma), len(mb), "common", len(common), "df_mismatch", ddf)
    for label, sel in (("gamma/invariant/none", lambda m: "+R" not in m), ("FreeRate +R", lambda m: "+R" in m)):
        d = [abs(ma[m][0] - mb[m][0]) for m in common if sel(m)]
        print("  %-22s %3d models  identical %3d  max_abs_dlnL %.6f" % (label, len(d), sum(x == 0 for x in d), max(d or [0])))


if sys.argv[1] == "--models":
    compare_models(models(sys.argv[2]), models(sys.argv[3]))
    sys.exit(0)
a, b = sys.argv[1], sys.argv[2]
same = open(os.path.join(a, "real.treefile"), "rb").read() == open(os.path.join(b, "real.treefile"), "rb").read()
ta, sa = newick_splits(open(os.path.join(a, "real.treefile")).read())
tb, sb = newick_splits(open(os.path.join(b, "real.treefile")).read())
assert ta == tb, "different taxon sets"
shared = set(sa) & set(sb)
rf = len(sa) + len(sb) - 2 * len(shared)
print("treefile_byte_identical", same)
print("ml_tree_RF", rf, "of max", len(sa) + len(sb), "(normalised %.4f)" % (rf / (len(sa) + len(sb))))
dsup = [abs(sa[s] - sb[s]) for s in shared if sa[s] is not None and sb[s] is not None]
print("ufboot_support_shared_splits", len(dsup), "max_abs_diff %.0f" % max(dsup or [0]),
      "mean_abs_diff %.2f" % (sum(dsup) / len(dsup) if dsup else 0))
la, lb = iqtree_logl(os.path.join(a, "real.iqtree")), iqtree_logl(os.path.join(b, "real.iqtree"))
print("final_logL %.4f %.4f diff %.4f" % (la, lb, la - lb))
ma, mb = models(os.path.join(a, "real.model.gz")), models(os.path.join(b, "real.model.gz"))
compare_models(ma, mb)
bic = lambda m, n: {k: -2 * v[0] + v[1] * math.log(n) for k, v in m.items()}
n = int(re.search(r"with (\d+) amino-acid sites", open(os.path.join(a, "real.iqtree")).read()).group(1))
print("chosen", min(bic(ma, n).items(), key=lambda x: x[1])[0], min(bic(mb, n).items(), key=lambda x: x[1])[0])
ia = open(os.path.join(a, "real.log")).read()
ib = open(os.path.join(b, "real.log")).read()
it = lambda s: re.search(r"Total number of iterations: (\d+)", s).group(1)
print("search_iterations", it(ia), it(ib))
